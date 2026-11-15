-- What each child has booked, and what is open or missed; newest first.
SELECT child, kind, state, replace(starts_local, 'T', ' ') AS starts, minutes,
       minutes_source AS length_from, deadline, title, updated_by, substr(updated_at, 1, 16) AS updated_utc
FROM v_bookings
ORDER BY coalesce(starts_local, deadline) DESC, child;
