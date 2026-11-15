"""Layer 4: the board's delivery state in item_workflow: delivered, acknowledged,
completed, dismissed, restored.

`SnapshotStore` combines this repository with the other layers. Delivery
state follows stable keys independently of archival state.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from skoleklar.store.capture import utc_now

class DeliveryRepository:
    connection: sqlite3.Connection

    def record_delivery(self, item_ids: list[int]) -> None:
        if not item_ids:
            return
        now = utc_now()
        placeholders = ",".join("?" for _ in item_ids)
        self.connection.execute(
            f"""
            UPDATE item_workflow
            SET first_delivered_at = COALESCE(first_delivered_at, ?),
                last_delivered_at = ?
            WHERE stable_key IN (
                SELECT stable_key FROM derived_items WHERE id IN ({placeholders})
            )
            """,
            [now, now, *item_ids],
        )
        self.connection.commit()

    def record_delivery_keys(self, stable_keys: list[str], *, at: str) -> int:
        workflow_keys = self._workflow_keys_for_delivery_keys(stable_keys)
        if not workflow_keys:
            return 0
        placeholders = ",".join("?" for _ in workflow_keys)
        self.connection.execute(
            f"""
            UPDATE item_workflow
            SET first_delivered_at = COALESCE(first_delivered_at, ?),
                last_delivered_at = CASE
                    WHEN last_delivered_at IS NULL OR last_delivered_at < ? THEN ?
                    ELSE last_delivered_at
                END
            WHERE stable_key IN ({placeholders})
            """,
            [at, at, at, *workflow_keys],
        )
        self.connection.commit()
        return len(workflow_keys)

    def complete_delivery_keys(self, stable_keys: list[str], *, at: str) -> int:
        workflow_keys = self._workflow_keys_for_delivery_keys(stable_keys)
        if not workflow_keys:
            return 0
        placeholders = ",".join("?" for _ in workflow_keys)
        self.connection.execute(
            f"""
            UPDATE item_workflow
            SET completed_at = COALESCE(completed_at, ?),
                last_seen_at = CASE
                    WHEN last_seen_at < ? THEN ?
                    ELSE last_seen_at
                END
            WHERE stable_key IN ({placeholders})
            """,
            [at, at, at, *workflow_keys],
        )
        self.connection.commit()
        return len(workflow_keys)

    def _workflow_keys_for_delivery_keys(self, stable_keys: list[str]) -> list[str]:
        keys = sorted(set(stable_keys))
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"""
            SELECT stable_key FROM derived_items
            WHERE stable_key IN ({placeholders})
            UNION
            SELECT derived.stable_key
            FROM consolidated_items consolidated
            JOIN consolidation_members member
              ON member.consolidated_item_id = consolidated.id
            JOIN derived_items derived ON derived.id = member.derived_item_id
            WHERE consolidated.stable_key IN ({placeholders})
            UNION
            SELECT stable_key FROM message_event_facts
            WHERE stable_key IN ({placeholders})
            """,
            [*keys, *keys, *keys],
        ).fetchall()
        return sorted(str(row["stable_key"]) for row in rows)

    def update_item_workflow(self, item_id: int, action: str) -> dict[str, Any]:
        if item_id < 0:
            row = self.connection.execute(
                "SELECT stable_key FROM message_event_facts WHERE id = ?",
                (-item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown message event item id: {item_id}")
            columns = {
                "acknowledge": "acknowledged_at",
                "complete": "completed_at",
                "dismiss": "dismissed_at",
            }
            column = columns.get(action)
            if column is None:
                raise ValueError(
                    "Message event items support acknowledge, complete, or dismiss"
                )
            now = utc_now()
            self.connection.execute(
                f"UPDATE item_workflow SET {column} = ?, last_seen_at = ? "
                "WHERE stable_key = ?",
                (now, now, row["stable_key"]),
            )
            self.connection.commit()
            return {"item_id": item_id, "action": action, "at": now}
        if action == "restore":
            row = self.connection.execute(
                "SELECT stable_key FROM derived_items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown derived item id: {item_id}")
            now = utc_now()
            self.connection.execute(
                "DELETE FROM item_archive WHERE stable_key = ?", (row["stable_key"],)
            )
            self.connection.execute(
                "UPDATE item_workflow SET last_seen_at = ? WHERE stable_key = ?",
                (now, row["stable_key"]),
            )
            self.connection.commit()
            return {"item_id": item_id, "action": action, "at": now}
        columns = {
            "acknowledge": "acknowledged_at",
            "complete": "completed_at",
            "dismiss": "dismissed_at",
        }
        column = columns.get(action)
        if column is None:
            raise ValueError(f"Unknown item action: {action}")
        row = self.connection.execute(
            "SELECT stable_key FROM derived_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown derived item id: {item_id}")
        now = utc_now()
        self.connection.execute(
            f"UPDATE item_workflow SET {column} = ?, last_seen_at = ? WHERE stable_key = ?",
            (now, now, row["stable_key"]),
        )
        self.connection.commit()
        return {"item_id": item_id, "action": action, "at": now}
