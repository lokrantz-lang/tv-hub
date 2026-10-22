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

## Profiler

Fliken **Profiler** i webbadminen. Från början finns Henrik, Tillsammans, Ida, Linn och Gäst (tidigare tittarlägen blev profiler).

- **Egna favoriter, Fortsätt titta, sedda avsnitt och snabbval** per profil. Favoriter från tiden före 2.5 hör till Henrik.
- **Byta profil:** TV: den runda bilden överst i menyn till vänster. Telefon: den runda bilden uppe till höger.
  Ingen "Vem tittar?" vid start: boxen startar med sin *startprofil*.
- **Kod (valfri, 4–8 siffror):** appen frågar efter den när någon byter till profilen.
- **Får använda vuxenläget:** bara profiler med den rutan ikryssad kan slå på vuxenläget. Minst en profil måste ha den.
- **Gäst:** sparar inga favoriter, inget Fortsätt titta och inga tummar.
- **Lägg till, byt namn, färg, emoji och ta bort** i fliken. Tar man bort en profil försvinner dess favoriter, Fortsätt titta,
  tummar och snabbval.
- **Per box** (fliken *Boxar*): *Startprofil*, *Tillbaka till startprofilen efter X h utan användning* och
  *Kräv kod för vuxenläget*. Vid uppdateringen till 2.5 fick en box med "vardagsrum" i namnet startprofilen Tillsammans,
  4 timmar och kod; en box med "sovrum" i namnet fick Henrik och ingen kod.

### Mina program (snabbval)

Överst på Hem och under Favoriter → *Mina program*. Läggs till per profil i fliken Profiler. *Lägg till förslagen* ger
Rapport (SVT1), TV4 Nyheterna (19:00 och 22:00) och Robinson (TV4). Sätten:

| Sätt | Spelar |
|---|---|
| Senaste sändningen | Direkt om programmet sänds nu, annars den senaste sändningen ur kanalens arkiv. |
| Senaste huvudsändningen | Bara sändningar som börjar vid angivna tider (±20 min), t.ex. 19:00 och 22:00. |
| Avsnitt ur arkivet i tur och ordning | Påbörjat avsnitt först, annars nästa efter det du såg senast, annars det senaste. Snabbast för nya avsnitt. |
| Serie: fortsätt där du var | En serie i Toucans bibliotek. Nya avsnitt kommer ofta dit senare än i arkivet. |

Programmets namn matchas mot programguiden (stora/små bokstäver och tecken spelar ingen roll). Kanalen måste ha arkiv
(catch-up) för allt utom det som sänds nu; arkivet är oftast några dagar. *Testa* i webbadminen visar vad som skulle spelas.

## Mina sporter och När går …?

**Sport** i menyn på TV:n och raden *Sport: nu och snart* på Hem (TV och telefon). Sporterna och lagen ställs in i
webbadminen under fliken **Sport**. Från början: Golf, Ridsport, Norrköping Dolphins, IFK Norrköping och
Sveriges fotbollslandslag (dam och herr).

- **Just nu** (OK: titta), **Kommande** (OK: påminn mig eller spela in) och **I efterhand** (OK: spela ur arkivet).
  Grupperna överst visar en sport eller ett lag i taget. Samma sändning på flera kanaler visas en gång ("även på …").
- **Sökorden** jämförs med programguidens titel (och beskrivning, om inte *Bara i titeln* är ikryssat). En rad per
  alternativ, `dolphins + basket` betyder att båda orden måste finnas, och *Inte*-orden stoppar en träff (t.ex.
  `miami` så att Miami Dolphins inte kommer med). *Ändra och se sändningar* visar direkt vad som hittas.
- **Fråga när den börjar:** de valda boxarna frågar "Byt kanal?" några minuter innan, och telefonen får en notis
  (notify-tjänsten under *Boxar → Påminnelser till telefonen*). Inte mellan 23 och 08 som standard.
- **Spela in lagets sändningar:** kommande sändningar planeras som inspelningar på vald box, 2 minuter före till
  15 minuter efter. Tar du bort en sådan inspelning planeras den inte igen. Boxen måste vara på eller i standby,
  och en inspelning använder en av kontots anslutningar (anslutningsvakten frågar som vanligt).
- **När går …?** Sök på TV:n letar också i programguiden: *Sänds nu*, *Kommande sändningar* och *Se i efterhand*
  överst bland träffarna. Knappen *När går …?* på Sport-sidan går dit. I webbadminen finns samma sökning under Sport.

**Programguiden** hämtas i bakgrunden var fjärde timme för svenska kategorier och sportkategorier (en kanal i
taget, med paus, aldrig vuxenkategorier). Kanaler med samma programguide hämtas en gång. Valet kan ändras under
*Sport → Programguiden → Kanaler som söks igenom*. Det som inte finns i Toucans programguide hittas inte, och
matcher som bara ligger på tillfälliga eventkanaler utan programguide syns inte.

## TV-guiden

**TV-guide** i menyn (eller Guide-knappen när ingen kanal är markerad): kanalerna i rader och programmen på en
tidslinje, två timmar i taget. ▲ ▼ byter kanal, ◀ ▶ går mellan programmen, Kanal +/− bläddrar en sida. OK på ett
pågående program visar kanalen, på ett tidigare program spelas det ur arkivet (▶ framför titeln), och på ett
kommande program kan du få en påminnelse eller spela in. Håll in OK för alla val. Upp från första raden väljer
grupp: Favoriter, Senaste kanaler eller en kategori. Guiden sträcker sig ett dygn bakåt och drygt ett dygn framåt.

**Spelarlisten** för live visar programmets namn, tider, minuter kvar, hur långt programmet har kommit,
vad som kommer sedan och klockan.

## Dolt vuxenläge

Kategorier med "For Adults", "Porn" eller "XXX" i namnet räknas som vuxeninnehåll. Under **Kategorier** i webbadminen
kan en kategori markeras som *Alltid 18+* eller *Aldrig 18+*. I boxen: markera **Inställningar** och tryck **vänster tre gånger**.

I vuxenläget döljs favoriter, Fortsätt titta, senaste kanaler och sedda. Tryck **höger tre gånger** på **Inställningar**
för att visa dem, och igen för att dölja dem. Tittandet sparas alltid, men visas bara när du har valt att visa det.
Läget stängs av efter 15 minuter utan aktivitet, efter högst 3 timmar och när appen eller boxen startas om.
En box med innehållet *Utan vuxenläge* eller *Bara gruppen …* kan inte byta läge, och bara profiler med
*Får använda vuxenläget* kan slå på det (för andra händer ingenting). Läget stängs av när någon byter profil.

**PIN-kod (valfritt):** webbadminen, *Boxar → PIN-kod för vuxenläget*. Med en kod frågar boxen och telefonen efter
den efter de tre trycken, på boxar där *Kräv kod för vuxenläget* är ikryssat. Utan kod fungerar det som förut. Koden sparas krypterad och kan bara bytas, inte visas.
Efter fem fel i rad går det inte att försöka igen på 5 minuter.

## Utseende i boxarna

Appen startar i det **moderna utseendet**: meny med ikoner till vänster, Hem med stora bilder och rader,
Favoriter, Live-TV, Filmer, Serier, Sök och Inspelningar. Det **klassiska utseendet** med tre kolumner finns kvar:
välj **Inställningar → Utseende** i appen. Valet sparas per box.

- **Visa:** i Filmer, Serier och Favoriter väljer knappen *Visa* (bredvid Sortera) mellan *Affisch med titel*,
  *Lista* (liten bild med år, längd, genre, betyg och handling) och *Bara bild*. Valet sparas per sida och box.
- **Favoriter** i menyn: grupperna Filmer, Serier, Avsnitt och Kanaler. Filmer och Serier har också gruppen ★ Favoriter.
- **Tillbaka** går till Hem från alla sidor. På Hem går Tillbaka först upp till första raden och frågar sedan om appen ska stängas.
- **Sök med rösten:** 🎤 överst på tangentbordet i Sök öppnar Googles röstinmatning. Det du säger blir sökningen.
- **Nytt per dag:** *Inställningar → Nytt per dag* visar, per dag de senaste 14 dagarna, kategorierna som fått något nytt.
- **Starta på senaste kanalen:** *Inställningar*. Appen öppnar då kanalen du såg sist (aldrig en vuxenkanal).
- **Ta bort från Fortsätt titta:** håll in OK på kortet.

Under **Kategorier** i webbadminen kan en kategori få ett eget namn (*Eget namn*) och väljas som
**Start**: Filmer, Serier eller Live öppnar då i den kategorin, både på TV:n och i telefonen.

## Inspelning

Inspelningarna sparas på ett USB-minne i boxen. Webbadminen håller schemat och visar status.

1. Sätt i ett USB-minne i boxen. Det ska vara formaterat som FAT32 eller exFAT.
2. Planera en inspelning på något av följande sätt:
   - **Programguiden i boxen:** håll in OK på en kanal, välj *Programguide*, tryck OK på ett kommande program och välj *Spela in*.
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
- **Vuxeninspelningar** sparas i en dold mapp (`.privat`) med neutrala filnamn (`rec-123.ts`) och en `.nomedia`-fil, så att galleri- och videoappar inte visar dem. Notisen under inspelning visar bara "TV Hub spelar in". I webbadminen syns de först när du kryssar i *Visa 18+*. Äldre vuxeninspelningar flyttas dit automatiskt.

## Mina media (NAS och USB-disk)

*Mina media* i menyn på boxarna visar filmer, hemvideo och bilder från NAS:en och från en USB-disk i boxen.
Uppspelningen går inte via Toucan och räknas inte som en anslutning där.

- **NAS:en** hittas av sig själv om den har en mediaserver (DLNA). På en Synology: paketet **Media Server**.
  Vilka mappar som syns styrs i DSM under *Kontrollpanelen → Indexeringstjänst → Indexerad mapp*.
  Hittas den inte: *Lägg till NAS…* uppe till höger och skriv in dess IP-adress.
- **USB-disken** syns bara i **vuxenläget**. Den dyker upp när den sätts i. Första gången frågar boxen om TV Hub får läsa filer: svara *Tillåt*.
- **Favoriter:** håll in OK på en mapp, video eller bild → *☆ Lägg till i favoriter*. De visas under *Favoriter* i grupperna
  **NAS** och **USB-disk**. De sparas bara på den boxen. USB-favoriter syns bara i vuxenläget med historik påslagen (höger tre gånger på Inställningar).
- **Mappar som på disken.** OK öppnar, Tillbaka går upp en mapp. *Sortera* (namn eller datum) och *Visa* fungerar som i Filmer.
- **Film och video** spelas i TV Hubs spelare och fortsätter där du slutade. Filerna i en mapp spelas i ordning
  (t.ex. avsnitt). En `.srt` med samma namn som filmen läses in (USB).
- **Bilder:** OK visar bilden, vänster/höger bläddrar, OK startar bildspelet. Håll in OK på en mapp för *Bildspel av mappen*.
- **Alla videor, nyast först:** *Sortera → Alla videor i undermapparna*, eller håll in OK på en mapp. Visar videorna i mappen
  och alla undermappar i datumordning. Datumet tas ur filnamnet (t.ex. `VID_20190612_…`) och annars från NAS:en.
- **Filer boxen inte kan avkoda** (vanligt för gamla DivX/Xvid-filmer) öppnas automatiskt i **VLC** om den finns på boxen
  (gratis i Play Butik). I VLC sparar TV Hub ingen position, det gör VLC själv.
- **Ingenting härifrån visas i Fortsätt titta på Hem.**
- **Dölj:** håll in OK på en NAS-mapp → *Dölj mappen*. Dolda mappar, och mappar med XXX, 18+, Adult eller Porn i namnet, syns bara i vuxenläget.
- **Begränsningar:** filerna spelas som de är (ingen omkodning). Tunga 4K-filer kan hacka över wifi och på Z Alpha.
  Filmer med bara DTS- eller TrueHD-ljud kan bli tysta: välj ett annat ljudspår. ISO-filer och Blu-ray-mappar fungerar inte.
  USB-disken: FAT32 och exFAT fungerar, NTFS beror på boxen. En 2,5"-disk kan behöva egen ström.

## Påminnelser och insomningstimer

- **Påminn mig:** i programguiden, OK på ett kommande program → *Påminn mig när det börjar*. När programmet börjar frågar
  boxen om den ska byta kanal. Med en notify-tjänst vald i webbadminen (*Boxar → Påminnelser till telefonen*) kommer också en
  notis till telefonen. Boxen måste vara på. Är TV Hub stängd på Z10 Pro krävs "Visa ovanpå andra appar" (se Röststyrning).
- **Insomningstimer:** under uppspelning, OK två gånger (eller *Mer* på telefonen) → *Insomningstimer*: 30, 60 eller 90 minuter,
  2 timmar eller när programmet, filmen eller avsnittet slutar. En minut innan visas en varning. Tryck valfri knapp för att fortsätta titta.

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

- **Tittarlägen** är profilerna: egna tummar, egen logg och egna regler (barnvänligt, högsta längd, genrer att undvika, titta igen efter X dagar). Reglerna ändras i webbadminen under Filmkväll. Att välja ett tittarläge i appen byter profil.
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
| Gul | Favoriter (sidan) | Från programmets början |
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
  en film, Filmkväll, matchen som sänds nu (Mina sporter, ett lag eller alla), pausa, fortsätta eller stänga.
  Sänds ingen match säger webbadminens *Testa* när nästa går. *Lägg till standardkommandon* skapar
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

Se `homeassistant/tv_hub_package.yaml` i förrådet. Det ger statussensor, svarstid, vem som tittar, utgångsdatum och notiser,
och för Mina sporter: `sensor.tv_hub_nasta_match` (titel, med kanal, tid och om den pågår som attribut),
`sensor.tv_hub_nasta_match_borjar` (tidpunkt) och `binary_sensor.tv_hub_match_pagar`.
