# Cheapy Packaged Data

Cheapy ships a hand-curated airport snapshot. It is not a full airport database.

## airports.v1.json

Airport records are a manual curated snapshot derived from OurAirports public-domain airport data.

Source: https://ourairports.com/data/
Data dictionary: https://ourairports.com/help/data-dictionary.html
Retrieved: 2026-05-28
License note: public domain / no guarantee of accuracy
Selection method: manual curated global airport snapshot for Cheapy flight-search coverage

The snapshot includes a curated set of major Vietnam, regional, long-haul, and global hub airports used by Cheapy search workflows. It includes common Europe/Asia/North America hubs and user-facing routes such as DUS to CXR, but it is still intentionally smaller than a full IATA catalog.

## hubs.v1.json

Hub candidates are manually curated from the Wikipedia "List of hub airports" page.

Source: https://en.wikipedia.org/wiki/List_of_hub_airports
Revision: https://en.wikipedia.org/w/index.php?oldid=1344170237&title=List_of_hub_airports
Retrieved: 2026-05-28
License: Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)
License URL: https://creativecommons.org/licenses/by-sa/4.0/
Attribution: Wikipedia contributors, "List of hub airports," Wikipedia revision oldid=1344170237.
Modification notice: manual curated excerpt with modified local tiering for Cheapy global routing experiments.
Selection method: manual curated global hub excerpt

Cheapy stores only airport codes and manually assigned local tiers. The full Wikipedia table is not copied into this repository.
