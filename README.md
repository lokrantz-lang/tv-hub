# TV Hub

Egen IPTV-lösning för Formuler Z10 Pro och Z Alpha, med Home Assistant som nav.

```
 Toucan (Xtream)  ──ström──▶  TV-boxarna (TV Hub-appen)
        ▲                          │  frågar om adress, skickar mätvärden
        │ inloggningskontroller    ▼
        └──────────────  Home Assistant: TV Hub-tillägget  ◀── webbadmin via Nabu Casa
```

| Del | Mapp | Körs på |
|---|---|---|
| Backend och webbadmin | `tv_hub/` | Home Assistant OS (Dell OptiPlex), som tillägg |
| Android TV-app | `android/` | Z10 Pro (Android 10) och Z Alpha (Android 7) |
| Sensorer och notiser | `homeassistant/` | Home Assistant |
| Tester | `tests/` | Datorn eller GitHub |

## Vad den gör

- **Portaler med automatiskt byte.** Inloggningen kontrolleras varannan minut. Boxarna får alltid en portal som fungerar, och byter själva om strömmen dör.
- **Felsökning som pekar ut orsaken:** internet, källan, hemnätet, kontot eller en annan app som strömmar. Varje slutsats visar sitt underlag.
- **Varför listan inte uppdaterades.** Varje steg i listuppdateringen loggas med orsak och tid. En tom eller kraftigt förkortad lista från källan skriver aldrig över den gamla.
- **Bibliotek med sök, sortering, filter, affischvy, favoriter och egna grupper** i webbadminen, som syns direkt i boxarna.
- **Inspelning till USB-minnet i boxen och timeshift** (paus och spolning i direktsändningar via Toucans arkiv).
- **Anslutningsvakt.** Toucan stänger av konton som strömmar på två enheter samtidigt. Den andra boxen frågar först och väntar tills den första har stoppat. Servern startar aldrig egna strömmar i bakgrunden.

## Installation

### 1. Lägg koden i ett GitHub-förråd

Home Assistant installerar tillägg från ett Git-förråd, och GitHub bygger appen åt dig.

1. Skapa ett **publikt** förråd, t.ex. `tv-hub`. Det innehåller inga lösenord, och Home Assistant kan inte läsa privata förråd.
2. Byt `lokrantz-lang` mot ditt GitHub-namn i `repository.yaml` och `tv_hub/config.yaml`.
3. Ladda upp innehållet och lägg in hemligheterna från `github-hemligheter.txt` (Settings > Secrets and variables > Actions).
4. Under **Actions** körs bygget. När det är grönt finns appen på
   `https://github.com/lokrantz-lang/tv-hub/releases/download/apk-latest/tvhub.apk`.

### 2. Installera tillägget i Home Assistant

1. **Inställningar > Tillägg > Tilläggsbutiken > ⋮ > Förråd**, lägg till `https://github.com/lokrantz-lang/tv-hub`.
2. Installera **TV Hub**, slå på *Visa i sidomenyn* och starta.
3. Öppna TV Hub i sidomenyn:
   - **Portaler:** samma URL, användarnamn och lösenord som i MyTVOnline.
   - **Översikt:** *Uppdatera listor*.
   - **Boxar:** *Para ny box* ger en sexsiffrig kod.

### 3. Installera appen på boxarna

1. Installera appen **Downloader** (från Formulers appbutik eller Google Play).
2. Tillåt installation från okända källor för Downloader (Inställningar > Säkerhet).
3. Skriv in APK-adressen ovan i Downloader och installera.
4. Starta **TV Hub**, ange `http://<Home Assistants IP>:8099` och koden.

**Stäng MyTVOnline helt** innan du tittar i TV Hub. Två appar som strömmar samtidigt räknas som två anslutningar hos Toucan. TV Hub varnar om Toucan rapporterar en anslutning som inte kommer från TV Hub.

### 4. Sensorer i Home Assistant (valfritt)

Se `homeassistant/tv_hub_package.yaml`. Ger status, svarstid, vem som tittar, när abonnemanget går ut och en notis vid problem.

## Fjärrkontrollen (modernt utseende)

| Var | Knapp | Gör |
|---|---|---|
| Menyn till vänster | Upp/ner, höger | Välj sida, gå in |
| Kort och listor | OK / håll in OK | Öppna / fler val (favorit, programguide) |
| Var som helst | Tillbaka | Till menyn; två gånger i menyn stänger appen |
| Film och avsnitt | OK, vänster/höger | Paus, spola 10 s / 30 s |
| Inställningar | Vänster ×3 / höger ×3 | Vuxenläge / historik i vuxenläget |

## Fjärrkontrollen (klassiskt utseende)

| Var | Knapp | Gör |
|---|---|---|
| Menyn | OK | Öppna i helskärm |
| Listor | OK | Spela kanal, öppna film eller serie |
| Listor | Höger | Helskärm |
| Listor | Håll in OK | Fler val: favorit, programguide, detaljer |
| Helskärm | Upp till översta raden | Sortera och Filter |
| Helskärm | Tillbaka, eller vänster från första kolumnen | Tillbaka till tre kolumner |
| Menyraden Inställningar | Vänster tre gånger | Dolt vuxenläge på/av |
| Menyraden Inställningar (i vuxenläget) | Höger tre gånger | Visa/dölj favoriter och historik |
| Menyn | Tillbaka två gånger | Stäng appen |
| Film, avsnitt, inspelning | Vänster / höger | Spola 10 s bakåt / 30 s framåt (håll in för snabbare) |
| Sparat filter i menyn | Håll in OK | Ta bort |
| Live | Upp/ner, CH+/CH− | Byt kanal |
| Live | Vänster eller 0 | Förra kanalen |
| Live | Play/paus | Pausa (timeshift) |
| Live | ◀◀ ▶▶, eller vänster/höger när du är pausad | Spola en minut |
| Live | ■ | Tillbaka till direkt |
| Programguide | OK på kommande program | Spela in |
| Live | OK, OK igen | Info, sedan ljud, undertexter, programguide, spola och spela in |
| Film/serie | Menyknappen | Ljud och undertexter |
| Fel | OK | Försök igen |

## Säkerhet

- Webbadminen svarar bara via Home Assistants Ingress (inloggning och tvåfaktor via Nabu Casa).
- Telefonen utanför hemmet: via Nabu Casa och Home Assistants Ingress, med en vanlig (icke-admin) HA-användare plus telefonens egen nyckel. Webbadminen spärras för alla som inte är admin i HA.
- Port 8099 används av boxarna i hemnätet och kräver en nyckel per box. Öppna inte porten i routern.
- Toucan-lösenordet ligger i tilläggets databas i Home Assistant och ingår i HA:s säkerhetskopior.

## Utveckling

```
python3 tests/test_e2e.py     # 355 kontroller mot en simulerad Xtream-server
python3 tests/demo.py         # demodata: admin på http://127.0.0.1:28080
```

Backenden använder bara Pythons standardbibliotek. Appen använder Kotlin, Media3 och RecyclerView, med minSdk 24.

## Kända begränsningar

- Mobilläget och vägen via Nabu Casa har provats mot en simulerad Home Assistant, inte på en riktig telefon.
- Har inte provats mot Toucans riktiga server eller på boxarna. Xtream-API:t är standardiserat, men små avvikelser kan finnas.
- Programguiden visas per kanal, inte som ett rutnät för alla kanaler samtidigt.
- Catch-up använder Xtreams standardadress (/timeshift/). Om Toucan använder en annan variant behöver den justeras. Det påverkar också timeshift-spolning och inspelning av redan sända program.
- Inspelning till USB och väckning ur standby har inte provats på Formuler-boxarna.
