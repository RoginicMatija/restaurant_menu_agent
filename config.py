from pydantic import BaseModel, Field
from typing import List, Optional

class DailySpecial(BaseModel):
    item_name: str
    price: str
    category: str = Field(description="Italian, Asian, BBQ, Mexican, or Other")
    description: str

class RestaurantSummary(BaseModel):
    restaurant_name: str
    is_daily_menu_available: bool
    specials: List[DailySpecial]
    general_vibe: str = Field(description="Short summary of the restaurant style")

# The prompt that tells Gemini how to 'think'
MENU_PROMPT = """
Preuzmi ulogu strogog lokalnog gastro izviđača (food scout). 
Dobit ćeš sadržaj web stranice restorana u Markdown formatu.
Danas je {current_day} ({current_date}).

VAŽNO PRAVILO ZA DATUME I DNEVNE MENIJE:
1. Neki restorani u tekstu imaju ispisane dane (Ponedjeljak, Utorak...) ili datume. Ako u tekstu postoje dani/datumi, MORAŠ strogo izvući samo ona jela koja pripadaju današnjem danu ({current_day} ili {current_date}).
2. Međutim, restorani poput Cassandre imaju samo naslov poput "DNEVNI JELOVNICI" ili "Gableci", bez ispisanog datuma. Ako u tekstu NE POSTOJE ispisani dani u tjednu, a postoji ponuda gableca, MORAŠ pretpostaviti da je to današnji meni i izvući ta jela!

Slijedi ove korake:
1. Ako restoran ima dnevni meni za danas (prema gore navedenom pravilu), postavi is_daily_menu_available na True.
2. Izvuci sva jela iz dnevne ponude. Za svako jelo navedi točan naziv, točnu cijenu (uključujući valutu) i kratki opis (ako postoji, npr. prilozi).
3. Svako jelo kategoriziraj u Italian, Asian, BBQ, Mexican ili Ostalo.
4. Ako restoran izričito kaže da danas nema gableca, ili na stranici nema nikakve hrane, postavi is_daily_menu_available na False.

Sadržaj s web stranice:
{scraped_content}
"""

REGULAR_MENU_PROMPT = """
Preuzmi ulogu strogog lokalnog gastro izviđača (food scout). Dobit ćeš tekst s web stranice restorana.
Zadatak je izvući CIJELI standardni (a la carte) jelovnik s jelima i cijenama.

Pravila:
1. Izvuci SAMO standardni jelovnik (a la carte). NE uključuj gablece, dnevne menije ni ručak ponude.
2. Ako u tekstu postoje nazivi jela uz cijene (€, EUR, kn), postavi is_daily_menu_available na True.
3. Izvuci SVA jela koja su navedena u jelovniku — ne samo uzorak i ne samo prva tri. Ne preskači kategorije (predjela, juhe, glavna jela, pizze, deserti...).
4. Broj stavki u specials MORA odgovarati broju jela s cijenama u tekstu (npr. ako ima 40 cijena, vrati ~40 jela).
5. Za svako jelo navedi naziv, cijenu (s valutom) i kratki opis ako postoji. Preskoči jela bez cijene.
6. Kategoriziraj u Italian, Asian, BBQ, Mexican ili Ostalo.
7. Ignoriraj cookie poruke, footere i navigaciju — fokus samo na hranu.
8. Samo ako u tekstu stvarno nema nijednog jela ni cijene, postavi is_daily_menu_available na False.

Sadržaj s web stranice:
{scraped_content}
"""