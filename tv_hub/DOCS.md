# TV Hub

TV Hub gör Home Assistant till navet för din IPTV:

- **Portaler med automatiskt byte.** Lägg in Toucans adresser. TV Hub kontrollerar inloggningen varannan minut och boxarna använder alltid den som fungerar.
- **Felsökning.** När bilden stannar upp eller listorna inte uppdateras säger TV Hub om felet ligger hos källan, din internetleverantör, hemnätet eller kontot, och visar underlaget.
- **Bibliotek med sortering, filter och affischvy, favoriter och egna grupper.** Sök bland alla kanaler, filmer och serier från mobilen eller datorn. Allt syns direkt i boxarna.
- **Helskärm, sortering och filter** för filmer, serier och kanaler, med sparade filter som egna menyrader.
- **Fortsätt titta**, sedda avsnitt, senaste kanaler, programguide och catch-up.
- **Profiler per box**, dolda kategorier och bevakning av nya titlar.
- **Självuppdatering:** boxarna hämtar nya versioner av appen via Home Assistant.
- **Inspelning och timeshift.** Spela in till USB-minnet i boxen, pausa och spola i direktsändningar.
- **Anslutningsvakt.** Toucan tillåter bara en ström åt gången. Startar du en box medan en annan tittar frågar den först och stoppar den andra innan den börjar.

## Kom igång

1. Öppna **TV Hub** i sidomenyn.
2. **Portaler:** Lägg in samma portal-URL, användarnamn och lösenord som i MyTVOnline. Klicka *Spara och testa*.
3. **Översikt:** Klicka *Uppdatera listor*. Första gången tar det upp till ett par minuter.
4. **Boxar:** Klicka *Para ny box* och skriv in koden i TV Hub-appen på boxen. Serveradressen i appen är `http://<Home Assistants IP>:8099`.

## Dolt vuxenläge

Kategorier med "For Adults", "Porn" eller "XXX" i namnet räknas som vuxeninnehåll. Under **Kategorier** i webbadminen
kan en kategori markeras som *Alltid 18+* eller *Aldrig 18+*. I boxen: markera **Inställningar** och tryck **vänster tre gånger**.

I vuxenläget döljs favoriter, Fortsätt titta, senaste kanaler och sedda. Tryck **höger tre gånger** på **Inställningar**
för att visa dem, och igen för att dölja dem. Tittandet sparas alltid, men visas bara när du har valt att visa det.
Läget stängs av efter 15 minuter utan aktivitet, efter högst 3 timmar och när appen eller boxen startas om.
En box med profilen *Utan vuxenläge* eller *Bara gruppen …* kan inte byta läge.

## Utseende i boxarna

Appen startar i det **moderna utseendet**: meny med ikoner till vänster, Hem med stora bilder och rader,
Live-TV, Filmer, Serier, Sök och Inspelningar. Det **klassiska utseendet** med tre kolumner finns kvar:
välj **Inställningar → Utseende** i appen. Valet sparas per box.

Under **Kategorier** i webbadminen kan en kategori få ett eget namn (*Eget namn*) och väljas som
**Start**: Filmer, Serier eller Live öppnar då i den kategorin, både på TV:n och i telefonen.

## Inspelning

Inspelningarna sparas på ett USB-minne i boxen. Webbadminen håller schemat och visar status.

1. Sätt i ett USB-minne i boxen. Det ska vara formaterat som FAT32 eller exFAT.
2. Planera en inspelning på något av följande sätt:
   - **Programguiden i boxen:** håll in OK på en kanal, välj *Programguide*, och tryck OK på ett kommande program.
   - **Under uppspelning:** tryck OK två gånger och välj *Spela in*.
   - **Webbadminen:** fliken *Inspelningar*, välj box och kanal. Boxen hämtar schemat inom 20 minuter.
3. Titta under **Inspelningar** i boxens meny.

**Filmer och serier** kan också spelas in (laddas ner) till USB-minnet: knappen **⬇ Spela in** på filmens
detaljsida, **Spela in hela serien** på seriesidan, eller håll in OK på en film eller ett avsnitt.
Från webbadminen: öppna titeln i **Biblioteket** och välj box. Nedladdningen görs en i taget och tar
ungefär lika lång tid som Toucan och din uppkoppling tillåter. Den räknas som en anslutning hos Toucan.

- **Boxen spelar in själv.** Den måste vara påslagen eller i standby när programmet sänds. Inspelningen börjar 1 minut före och slutar 3 minuter efter programtiden.
- **Program som redan har sänts** hämtas från Toucans arkiv, om kanalen har arkiv.
- **Filerna** ligger i `Android/data/se.tvhub.box/files/Inspelningar` på USB-minnet och kan öppnas på en dator (VLC spelar dem).
- **Avinstallera inte appen.** Android raderar då appens mapp på USB-minnet. Uppdateringar påverkar inte inspelningarna.
- **Två strömmar.** Att titta på en kanal medan en annan spelas in ger två samtidiga anslutningar hos Toucan. Anslutningsvakten släpper igenom inspelningar.

## Timeshift

| Knapp under direktsändning | Gör |
|---|---|
| Play/paus | Pausa och fortsätt |
| ◀◀ / ▶▶ | Spola en minut bakåt eller framåt. Håll in för att spola längre. |
| Vänster/höger (när du är pausad eller bakåt i tiden) | Samma som ◀◀ / ▶▶ |
| ■, eller OK två gånger och *Tillbaka till direkt* | Hoppa ikapp direktsändningen |
| OK två gånger och *Från början av programmet* | Starta om pågående program |

- **Kanaler med arkiv hos Toucan** kan spolas så långt bakåt som arkivet räcker. Det gäller de flesta svenska huvudkanalerna.
- **Kanaler utan arkiv** kan bara pausas, och bara så länge boxens minne räcker: ett par minuter på Z Alpha och några minuter på Z10 Pro.

## Filmkväll

Välj vem som tittar och få tio förslag. TV: *Filmkväll* i menyn. Telefon: knappen överst. Webbadmin: fliken *Filmkväll*.

- **Tittarlägen** har egna tummar, egen logg och egna regler (barnvänligt, högsta längd, genrer att undvika, titta igen efter X dagar). Ändras i webbadminen. Valt läge gäller till kl 05 nästa morgon.
- **Tummar:** 👍👍 Älskade, 👍 Bra, 👎 Inte för oss. Håll in OK på en film eller serie, använd knappen på detaljsidan, eller svara när appen frågar efter filmen.
- **Förslagen** räknas i tillägget utifrån betyg, genre, regissör, skådespelare, årtionde, längd och om den är animerad, jämfört med det ni gillat. Sedda, nekade och "Inte i kväll" tas bort. Vill se kommer först.
- **Barnvänligt** bygger på genre och kategori (Animerat, Familj, Barn) och är en bedömning, inte en garanti.
- **Import:** IMDb (*Your Ratings → Export*) eller Letterboxd (*Settings → Data → Export*). Matchas på titel och årtal.
- **AI (valfritt):** OpenAI, Anthropic eller egen server som följer OpenAI:s standard (t.ex. Ollama: adress `http://<dator>:11434/v1`, ingen nyckel). Tjänsten får tittarlägets namn och smaktext, titlar ni betygsatt och sett, och ett urval kandidater. Utan tjänst: *Kopiera underlag* och klistra in svaret.

## Fjärrkontrollens knappar

| Knapp | I menyerna | Under uppspelning |
|---|---|---|
| ☰ Meny | Val för det markerade (som att hålla in OK) | Val i spelaren |
| ★ Favorit | Favorit på/av för det markerade | Favorit på/av (avsnitt: serien) |
| Röd | Filmkväll | Spela in programmet |
| Grön | Sök | Förra kanalen |
| Gul | Favoriter | Från programmets början |
| Blå | Nytt tillagt | Tillbaka till direkt |
| Info | Detaljer för det markerade | Programinfo |
| Guide | Programguide för markerad kanal | Programguide |
| ● Inspelning | Spela in det markerade | Spela in programmet |
| Siffror | Byt till kanalnumret (som i kanallistan) | Samma. Bara 0 = förra kanalen |
| Grupper (lär in den först) | Välj kategori i en lista | Kanallistan (eller avsnitten) |

Knapparna fungerar i det moderna utseendet och i spelaren. Olika fjärrar skickar olika koder för
★, Grupper och prickknapparna. Lär in dem under **Inställningar → Fjärrkontrollens knappar → Lär in en knapp**
och välj vad knappen ska göra i menyerna och under uppspelning.

## Röststyrning och fjärrstart

Säg till Google (eller tryck i telefonen eller webbadminen) och boxen startar uppspelningen.

- **Telefonen:** *📺 Spela på TV:n* på en film, serie, kanal eller ett avsnitt (håll in, eller knappen på detaljsidan).
- **Webbadminen:** *Spela på box* i Biblioteket.
- **Röstkommandon:** *Boxar → Röststyrning och fjärrstart*. Varje kommando blir ett skript i Home Assistant
  (`script.tv_hub_…`) som först kan slå på TV:n och byta källa och sedan säger åt boxen vad den ska göra:
  fortsätta titta, en serie där ni var, senaste programmet ur arkivet (t.ex. Rapport på SVT1), en kanal,
  en film, Filmkväll, pausa, fortsätta eller stänga. *Lägg till standardkommandon* skapar
  "Fortsätt titta i …", "Pausa …", "Spela vidare i …" och "Stäng TV Hub i …" för en box.

**Ställ in Google en gång:**

1. Exponera skripten mot Google Assistant i Home Assistant: *Inställningar → Röstassistenter → Exponera*
   (med Nabu Casa), eller lägg till dem i `google_assistant:` om du kör egen koppling.
2. Säg *"Hej Google, synka mina enheter"* (eller vänta en stund).
3. Skripten blir scener hos Google: *"Hej Google, aktivera Fortsätt titta i sovrummet"*. Vill du kunna säga
   bara *"Fortsätt titta i sovrummet"*: skapa en rutin i Google Home-appen med den frasen som startar scenen.

**Boxen måste vara på** (eller i standby med nätverket igång). Den lyssnar efter kommandon i bakgrunden.

- **Z10 Pro (Android 10):** för att kunna starta uppspelning när appen är stängd behövs behörigheten
  *Visa ovanpå andra appar*: *Inställningar → Röststyrning när appen är stängd* i appen. Utan den fungerar
  kommandona bara medan TV Hub är öppen.
- **Z Alpha (Android 7):** behöver ingenting.
- **TV:n:** skriptet kan slå på TV:n och byta källa om den finns i Home Assistant (t.ex. Samsung-integrationen).
  Slås en Samsung-TV inte på från avstängt läge: lägg till Wake on LAN enligt Samsung-integrationens dokumentation.
- Fjärrstart startar aldrig vuxeninnehåll, och boxen slår av vuxenläget innan den spelar.

## Telefonen och utanför hemmet

Samma app fungerar på en Android-telefon. På telefonen väljs mobilläget automatiskt.
Hemma pratar appen direkt med Home Assistant. Utanför hemmet går den via Nabu Casa.

**Ställ in en gång:**

1. Skapa en användare för appen i Home Assistant: *Inställningar → Personer → Lägg till person*.
   Namn och användarnamn `tvhub`. Slå på *Tillåt inloggning*. Slå **av** *Kan bara logga in från det
   lokala nätverket* och *Administratör*.
2. Skapa en nyckel för den användaren: logga in som `tvhub` i ett privat webbläsarfönster, klicka på
   namnet längst ner till vänster, fliken *Säkerhet* → *Långlivade åtkomsttoken* → *Skapa token*.
   Kopiera nyckeln och logga ut.
3. I TV Hub (som du själv, administratör): *Boxar → Telefon utanför hemmet*. Fyll i Nabu Casa-adressen
   (*Inställningar → Home Assistant Cloud*, adressen under Fjärrstyrning) och nyckeln. Tryck *Spara* och *Testa vägen*.
4. Para telefonen som en box, hemma på wifi. Appen hämtar uppgifterna för Nabu Casa automatiskt.

- Bara anrop till TV Hub går via Nabu Casa. Videon kommer direkt från Toucan (2–4 GB per timme i HD på mobildata).
- Toucan ser telefonens IP-adress när du tittar borta, och tittandet räknas som en anslutning.
- Användaren `tvhub` är inte administratör. Ta bort nyckeln i Home Assistant om telefonen försvinner.

**Chromecast:** tryck på bilden under en film eller ett avsnitt och välj *Casta*. Chromecasten hämtar videon
direkt från Toucan och telefonen blir fjärrkontroll (paus, spola, sluta casta). Fortsätt titta sparas som vanligt.

- Fungerar med MP4-filer. MKV fungerar bara på vissa enheter (oftast Chromecast med Google TV). Kanaler och arkiv kan inte castas.
- Telefonen och Chromecasten måste vara på samma wifi. På hotell blockeras det ofta. Toucan ser då hotellets IP-adress.
- Telefonen kan låsas medan den castar. Stäng spelaren för att sluta.
- **Bild-i-bild:** tryck på hemknappen under uppspelning så fortsätter den i ett litet fönster.

## Säkerhet

- Webbadminen nås bara genom Home Assistant (och därmed Nabu Casa med din inloggning och tvåfaktor). Den svarar inte på andra adresser.
- Vanliga användare i Home Assistant kan öppna tillägget (krävs för telefonen via Nabu Casa), men webbadminen visas bara för administratörer. Telefonen måste dessutom ha sin egen parningsnyckel.
- Port 8099 är till för boxarna i hemnätet. Den kräver en nyckel som boxen får vid parning. Öppna **inte** porten i routern.
- Lösenordet till Toucan sparas i add-on:ens databas i Home Assistant, ingår i säkerhetskopiorna och visas aldrig i webbadminen.

## Inställningar

| Inställning | Standard | Betydelse |
|---|---|---|
| Kontrollintervall | 120 s | Hur ofta inloggningen kontrolleras. Startar aldrig en ström. |
| Uppdatera listor | 12 h | Hur ofta kanal-, film- och serielistor hämtas. |
| Strömformat | auto | `ts` fungerar bäst med Formuler. `m3u8` om källan kräver det. |
| User-Agent | IPTVSmartersPlayer | Ändra bara om källan nekar anropen. |
| Referensservrar | 1.1.1.1, 8.8.8.8 | Skiljer internetavbrott från fel hos källan. |
| Timeout listuppdatering | 90 s | Höj om filmlistan ofta "svarade inte i tid". |
| GitHub-förråd för TV-appen | lokrantz-lang/tv-hub | Varifrån boxarna hämtar nya versioner. |

## Så tolkar TV Hub ett problem

| Vad TV Hub ser | Slutsats |
|---|---|
| Referensservrarna svarar inte | Din internetuppkoppling |
| Internet fungerar, men ingen portal svarar | Källan (Toucan) |
| Boxens hastighet i hemnätet under 30 Mbit/s | Wi-Fi eller kabeln till boxen |
| Hemnät och källans inloggning är bra, men strömmen kommer för långsamt | Källans strömserver eller din internetleverantör. Kapacitetstestet skiljer dem åt. |
| Toucan rapporterar fler anslutningar än TV Hub-boxar som tittar | En annan app strömmar, t.ex. MyTVOnline i bakgrunden |

## Home Assistant-sensorer

Se `homeassistant/tv_hub_package.yaml` i förrådet. Det ger statussensor, svarstid, vem som tittar, utgångsdatum och notiser.
