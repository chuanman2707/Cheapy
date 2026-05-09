# Cheapy Packaged Data

Gate 2 intentionally ships a tiny hand-curated data snapshot. It is not a full airport database.

## airports.v1.json

Airport records are a manual mini snapshot derived from OurAirports public-domain airport data.

Source: https://ourairports.com/data/
Data dictionary: https://ourairports.com/help/data-dictionary.html
Retrieved: 2026-05-09
License note: public domain / no guarantee of accuracy
Selection method: manual mini snapshot for Gate 2 MVP

The MVP snapshot includes only airports needed for the first Cheapy workflows and tests: CXR, SGN, HAN, DAD, PQC, SIN, BKK, KUL, TPE, HKG, ICN, NRT, DOH, DXB, LAX, SFO, JFK, LHR, CDG, FRA, SYD, MEL.

## hubs.v1.json

Hub candidates are manually curated from the Wikipedia "List of hub airports" page.

Source: https://en.wikipedia.org/wiki/List_of_hub_airports
Revision: https://en.wikipedia.org/w/index.php?oldid=1344170237&title=List_of_hub_airports
Retrieved: 2026-05-09
License: Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)
License URL: https://creativecommons.org/licenses/by-sa/4.0/
Attribution: Wikipedia contributors, "List of hub airports," Wikipedia revision oldid=1344170237.
Modification notice: manual curated excerpt with modified local tiering for Cheapy MVP routing experiments.
Selection method: manual curated excerpt for MVP routing experiments

Cheapy stores only airport codes and manually assigned MVP tiers. The full Wikipedia table is not copied into this repository.
