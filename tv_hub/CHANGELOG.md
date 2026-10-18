# Ändringar

## 1.3.0

- Inspelning till USB-minnet i boxen. Spela in från programguiden (OK på ett kommande program,
  håll in OK för fler val), från uppspelningen (OK två gånger) eller från mobilen under
  *Inspelningar* i webbadminen. Boxen startar inspelningen själv, även i standby och efter omstart.
- Program som redan har sänts hämtas från Toucans arkiv till USB-minnet.
- Inspelningar spelas upp under *Inspelningar* i boxens meny, även medan de spelas in, och
  fortsätter där du slutade.
- Man kan titta på en kanal och spela in en annan samtidigt. Det blir två anslutningar hos Toucan.
- Timeshift: Play/paus pausar live. ◀◀ och ▶▶ spolar en minut åt gången (vänster/höger när du är
  pausad eller bakåt i tiden). Kanaler med arkiv hos Toucan kan spolas så långt arkivet räcker,
  ända till programmets början. Övriga kan pausas några minuter. ■ eller "Tillbaka till direkt" hoppar ikapp.

## 1.2.0

- Helskärmsvy: OK på en kategori (eller höger från listan) visar innehållet över hela skärmen,
  filmer och serier som affischrutnät. Tillbaka eller vänster från första kolumnen går ur.
- Sortering (tillagd, betyg, årtal, längd, namn) och filter (betyg, längd, årtal, genre).
  Filter kan sparas som egna rader i menyn.
- Längd, genre, handling och skådespelare hämtas i bakgrunden, en film i taget.
- Detaljsida för filmer. Fortsätt titta, även från den andra boxen. Sedda filmer och avsnitt markeras.
- Senaste kanaler och "förra kanalen" (vänster eller 0 under uppspelning).
- Programguide per kanal med catch-up: se tidigare program i efterhand där Toucan sparar dem.
- "Nu"-program direkt i kanallistan.
- Val av ljudspår och undertexter (OK två gånger under live, menyknappen överallt). Valet sparas.
- Dölj och sortera kategorier i webbadminen.
- Profiler per box: allt, utan vuxenläge, eller bara en grupp (t.ex. barnens box).
- Bevaka titlar: notis i Home Assistant när något nytt dyker upp.
- Vuxenläget stängs av efter 15 minuter utan aktivitet, efter högst 3 timmar och vid omstart.
- Appen uppdaterar sig själv via Home Assistant.

## 1.1.0

- Dolt vuxenläge i boxarna. Vuxenkategorier och vuxentitlar känns igen automatiskt och visas
  bara i det dolda läget; i vanligt läge syns de inte i listor, sök, favoriter eller grupper.
- Webbadminen märker vuxeninnehåll med 18+.
- Rättat: fokus hoppar inte längre till menyn när man bläddrar förbi sista kategorin.

## 1.0.0

- Första versionen: portaler med automatiskt byte, Xtream-inloggningskontroller, internetkontroll,
  katalogsynk med skydd mot tomma listor, sök, favoriter, egna grupper, parning av boxar,
  anslutningsvakt, diagnostik med kapacitetstest, webbadmin via Ingress och sensorer för Home Assistant.
