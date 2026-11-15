# Notice

Required Notice: Copyright (c) 2026 Mads Bruun Hovgaard

Skolekit (the `skolekit` toolkit, the `skoleklar` family app, the firmware,
the deploy scripts, the documentation and the house artwork) is licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE.md).

## What that means

- **Free for noncommercial use.** A family, a hobbyist, a school, a charity or
  a public institution may use, change and share it, as long as this notice
  and the licence go with it.
- **No commercial use without a licence.** Selling it, offering it as a paid
  service, or using it inside a business needs a separate, written commercial
  licence from the copyright holder. Ask by opening an issue in the
  repository.
- **No warranty and no liability.** It comes as it is. You use it at your own
  risk, and the author is not liable for any damage or loss that follows from
  using it, as far as the law allows.

The licence text decides; this summary doesn't change it.

## Your own risk, in practice

This is an unofficial, personal project. It is not affiliated with, endorsed
by or supported by SkoleIntra, its vendor, or any school or municipality.

- It signs in to a school's site with a parent's own login. Whether that fits
  your school's terms of use is for you to check.
- It handles information about children. Keeping that information, the login,
  the device tokens and any cloud project safe is your responsibility, as is
  following the data protection law that applies to you.
- It can be wrong. A scan can fail, a page can change, and a model can misread
  a notice. Don't rely on the board or the phone alone for anything that
  matters; the school's own site is the source.
- Running it in Google Cloud and calling a model provider costs money, and
  those bills are yours.

## Parts under their own licences

| Part | Licence |
| --- | --- |
| Newsreader fonts in `src/skoleklar/fonts/` | SIL Open Font License 1.1 (`OFL-Newsreader.txt`) |
| The panel register sequence in `firmware/firebeetle-cloud/panel.cpp`, adapted from Waveshare | MIT (`firmware/firebeetle-cloud/PANEL-LICENSE.txt`) |

Dependencies installed from `pyproject.toml` and the Arduino libraries keep
their own licences.
