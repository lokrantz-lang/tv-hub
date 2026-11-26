# Ändringar

## 2.14.1

- **Säkerhet (viktigt med Funnel):** spärren mot gissning av koder kontrollerades först efter att koden prövats, så den stoppade
  inte en dator som gissar snabbt. Nu kontrolleras den före: efter tio fel stängs inloggningen i 15 minuter och ingen kod prövas,
  inte ens en rätt. Lovisas inbjudan gäller ett dygn och en enhet (tryck *Ny kod* för nästa). Du får en notis när en ny enhet loggar in.

## 2.14.0

- **Toucans guidefil (XMLTV):** många kanaler (bland annat V Sport Golf och Golf Channel US) saknar programguide när Toucan
  tillfrågas kanal för kanal. TV Hub hämtar nu också Toucans samlade guidefil (högst två gånger per dygn) och fyller de kanaler
  som saknar program. Webbadminen, fliken Sport → Programguiden, visar hur många kanaler filen hade och vilka som fick program,
  med knappen *Hämta guidefilen nu*. Toucans svar per kanal och tablån från TV4.se skrivs aldrig över.
- **Webbadminen:** flikarna bryts till två rader, så att alla syns.
- **Sök på boxen (ny app):** när du går till träffarna försvinner tangentbordet helt och träffarna får hela bredden, med sökordet
  ovanför. Vänster från första kortet tar tillbaka tangentbordet. *Rensa sökningen* ligger överst på tangentbordet.
- **Världscupen:** gamla Global Champions Tour-rader under *Sågs i guiden* rensas bort.
- **Webbappen via https (Tailscale Funnel):** omslag och kanallogor hämtas via TV Hub, eftersom webbläsaren stoppar bilder på http.
- Webbappen nås utifrån på `https://homeassistant.tail1d54b1.ts.net/web` (Funnel på port 443 till TV Hubs port 8099).

## 2.13.0

- **TV Hub i webbläsaren (laptopen):** öppna `http://<Home Assistants adress>:8099/web` och logga in med en kod (samma som när en
  box paras, eller Lovisas inbjudningskod). Hem, Live-TV med programguide, Filmer, Serier, Sport, Världscupen, Sök och Inspelningar,
  med profiler. Lovisas dator låses till hennes profil som telefonen.
- **Uppspelning i webbläsaren** via tillägget (`/web/stream/…`): webbläsaren får aldrig Toucans adress eller inloggning. Samma regler
  som boxarna: en ström åt gången, ta över, prioritet. Kanaler med AC3-ljud eller HEVC-bild kan sakna ljud eller bild i Chrome och
  Edge; då visas det, och *Spela på TV:n* finns på filmer.
- **Spela in från datorn** på vald box, och **Spela på TV:n** (skicka en film till en box som är på).
- Datorer visas inte i listan över boxar att skicka till eller spela in på.
- Utanför hemmet: med Tailscale (se DOCS, *Webbappen*).
- Sidan för nedladdning (`/hej`) visar också adressen till webbappen.
- Statusraden på Världscupen säger om tablån från TV4.se inte är hämtad än.

## 2.12.0

- **Världscupen, ny sida (ny app):** överst ett stort kort med det som sänds nu eller nästa sändning, nedräkning och knapparna Titta och
  Spela in. Under det säsongens etapper i en rad (flagga, datum, vinnare, Pågår/Nästa/Final). Välj en etapp så visas den under raden.
  Statusen för programguiden står längst ner i stället för som varningar överst.
- **Rättat:** Longines Global Champions Tour (Wien, Verona Playoffs, GCL Super Cup) räknades som världscupen eftersom sponsorn är densamma.
  Därför stod det "Sänds nu" vid Madrid. GCT hamnar nu bara under Sport → Ridsport.
- **Rättat:** Titta-knappen var vit med vit text, och vanliga knappar fick vit text på vit bakgrund med fokus.
- **Rättat:** varningen "kanalen skannas inte än" var fel. Den säger nu den riktiga orsaken (Toucan saknar guide för kanalen, och om tablån
  från TV4.se är hämtad).
- **Sport → Golf / Ridsport (ny app):** raden *Alla kanaler* med varje golf- respektive hästkanal och vad som sänds nu, och en rad per
  turnering ur film- och seriebiblioteket (till exempel Presidents Cup och The RSM Classic ur N1 Sports Events Replays), nyast först.
- Sportkanalerna läses alltid först i programguiden, så att de inte faller bort när listan är lång.
- **Exportera kanallista (webbadminen, fliken Världscupen):** en fil med alla kanaler, kategorier, arkiv och hur mycket programguide som
  finns, plus film- och seriekategorierna. Utan serveradress, inloggningsuppgifter och länkar.

## 2.11.0

- **Tablå från TV4.se (backend och webbadmin, ingen ny app krävs):** Toucans programguide saknar TV4, Sjuan och Sportkanalen, så världscupen och annan
  hoppning kunde aldrig hittas. TV Hub läser nu **TV4:s egen öppna tablå** (tv4.se/tabla, 15 dagar framåt, en sida per dag, med paus mellan) och
  lägger in programmen under *dina* kanaler (kanalen ur din Toucan-lista, med arkiv om den har). Därefter fungerar allt som förut och på riktigt:
  **Titta, Spela in, Ladda ner, notiser och Automatik** (spela in alla världscupssändningar på en box). Sändningar som finns i tablån
  visas som vanliga sändningar och inte längre som "Enligt tablå, ej bekräftad".
- Toucans egen guide skrivs aldrig över: tablån används bara för kanaler där Toucans guide saknas eller är kortare än tre dagar.
- **Kontroll i webbadminen:** fliken Världscupen har rutan *Tablå från TV4.se* med senaste hämtning, antal program per kanal, fel och
  vilka kanaler sidan hade, samt knappen *Hämta tablån nu*. Står det 0 program har TV4 ändrat sidan eller blockerat hämtningen; då syns det där.
- Tolkningen av TV4:s sida är skriven efter hur sidan beskrivs, inte provad mot den riktiga sidan (den når inte tillägget härifrån). Första
  hämtningen hos dig visar om den stämmer.
- TV4 Sportkanalen finns inte i tablån (A Coruña ligger kvar som "enligt tablå" tills vidare). Ridsport räknas nu som hoppning vid träff på världscupen.
- Innehåll från 2.10.2 ingår.
## 2.10.2

- **Världscupen, kräver ny app:** överst finns knapparna **Titta TV4**, **Titta Sjuan** och **Titta TV4 Sportkanalen** (dina egna kanaler ur listan) som
  startar kanalen direkt, så att du kan se det som sänds just nu även när Toucans guide saknar programmet (till exempel sammanfattningar).
  Kommande sändningar har **Öppna kanalen** för att testa att kanalen visar bild, och en rad som förklarar att Titta-knappen kommer när det börjar.
- Innehåll från 2.10.1 ingår.
## 2.10.1

- **Världscupen fungerar även när Toucans programguide saknar TV4, Sjuan och Sportkanalen** (som den gör hos dig just nu). Etapper med tid i
  den offentliga tablån (Verona, Stuttgart, Madrid, A Coruña) får en rad **Enligt tablå, ej bekräftad i din programguide**, med din egen
  Sjuan- eller Sportkanal-kanal från listan, rätt tid, och knapparna *Titta* (när det sänds) och *Spela in* (kommande). Redan sänt visas bara
  om kanalen har arkiv som räcker. Finns sändningen i guiden används den i stället, så ingen dubblas.
- Tiderna kommer från offentliga tablåer och kan ändras av TV4. Automatisk inspelning (Automatik) utgår från guiden och tar inte med
  tablåraderna; spela in dem med knappen.
- Innehåll från 2.10.0 ingår.
## 2.10.0

- **Lovisa (kräver ny app för inloggningen):** profilen *Lovisa* skapas automatiskt (ingen vuxenläge, ser allt i vanligt läge). På **Översikt**
  finns kortet *Bjud in Lovisa* med en åttasiffrig kod (gäller i 14 dagar, tre användningar, skapas om automatiskt). Hon öppnar
  `http://<Home Assistants IP>:8099/hej` på telefonen, trycker *Ladda ner TV Hub*, installerar, anger adressen och koden. Klart.
  Hennes enhet är **låst till hennes profil**: den ser bara Lovisa och servern svarar som Lovisa vad appen än skickar. Enheten får också
  använda telefonen utanför hemmet (Nabu Casa) om det är inställt under Boxar.
- **Du går före:** Toucan tillåter bara en ström. Börjar du titta medan Lovisa tittar stoppas hennes ström direkt med texten
  "Henrik började titta och går före." Hon kan inte ta över när du tittar, inte ens med tvång. Det gäller bara enheter som är låsta till en
  profil; mellan vanliga boxar är det som förut (frågan "tittar redan, ta över?"). Prioriteten sitter på profilen (ägaren 100, andra 0).
- **Lås en box till en profil** (Boxar → *Låst till profil*) för vilken enhet som helst.
- Appens kodruta tar nu sex eller åtta siffror.
- Världscupen: knappen *Spela in* visar "Går att spela in två veckor innan" för sändningar längre bort än två veckor i stället för att ge fel.
- Innehåll från 2.9.10 ingår.
## 2.9.10

- **Världscupen: rätt kanaler per etapp.** Enligt offentliga tablåer sänds Verona, Stuttgart och Madrid på **Sjuan**, och A Coruña på **TV4 Sportkanalen**
  (Madrid och A Coruña även på TV4 Play Plus Sport). Förut stod TV4 på tre av dem. TV4 Sportkanalen räknas nu som kanal som krävs i kontrollen.
  Tablåerna är inte verifierade mot ditt Toucan-konto; det visar kontrollen i fliken Världscupen.
- Innehåll från 2.9.9 ingår.
## 2.9.9

- **Världscupen i hoppning (ny flik i webbadminen, ny sida i appen, kräver ny app för appdelen):** planering, historik och kontroll mot dina
  kanaler på ett ställe. Säsongen 2026/27 ligger inlagd: de 13 etapperna i Western European League, finalen i Göteborg (mars 2027) och
  Sweden International Horse Show. TV4 har rättigheterna från i år (SVT visar finalen och mästerskapen tillsammans med TV4), och Verona sändes på
  Sjuan, så Sjuan och SVT 24 läses nu av programguiden för ridsport.
- **Stora knappar:** *Titta* (nu eller i efterhand), *Spela in* (kommande sändning, med två minuters marginal före och femton efter) och
  *Ladda ner* (redan sänd, hämtas ur kanalens arkiv). Inspelningar visas på sändningen (planerad, spelas in, klar). På boxen i menyn
  (ikon med hinder och stjärna), på telefonen via ikonen uppe till höger (där bara Titta finns, inspelning görs på boxen).
- **Historik:** programguiden minns bara en vecka, så sändningar som setts sparas. Tidigare etapper visar kanal, tid och, om du lagt in det,
  vinnare och häst (Oslo och Lyon är inlagda, resten fyller du i under *Spara resultat*).
- **Finns det i Toucan?** Webbadminen letar upp TV4, Sjuan, TV4 Sportkanalen, SVT, Eurosport, Horse & Country m.fl. i din kanallista och säger
  om de har arkiv och om programguiden läser dem, samt hur många världscupssändningar guiden har just nu. Det går inte att kolla
  utifrån, så kontrollen körs i ditt tillägg mot din riktiga lista.
- **Automatik:** välj en box som spelar in alla världscupssändningar automatiskt, och få notis när en börjar (intresset *Världscupen i hoppning*
  skapas första gången).
- Etapper utan fastställd tid visas som *Tid ej fastställd*; Amsterdam, Bordeaux och Helsingfors har preliminära datum.
- Innehåll från 2.9.8 ingår.

## 2.9.8

- **Spelaren (kräver ny app):** **OK pausar och fortsätter alltid**, även i direktsändning (förut öppnade OK en meny där).
  **Ett tryck ner** går direkt till Ljud i filmens tidslinje, och höger/vänster byter då mellan Ljud och Undertext i stället för att spola
  (det var ett fel: pilarna spolade även när knapparna var markerade). I direktsändning öppnar ner menyn med Ljudspår och Undertexter.
  Kanalbyte ner finns kvar på Kanal ner och Sida ner; pil upp byter fortfarande kanal uppåt. Info visar programinformation som förut.
- **Telefonen:** Androids vanliga tillbaka-knapp längst ner syns nu under uppspelning (bara statusfältet göms). Gäller telefoner med
  knappnavigering; med svepgester finns ingen knapp att visa.
- Innehåll från 2.9.7 ingår.

## 2.9.7

- **Portaler:** en riktig graf över svarstiden med dag/datum och klockslag på tidsaxeln och ms-skala. Tryck (eller håll musen) på grafen för
  detaljer om den tiden, dra åt sidorna för att gå bakåt i tiden, och välj 24 tim, 7 dagar eller 30 dagar. Röda fält är misslyckade kontroller.
- **Pausa, aktivera och arkivera portaler:** varje rad har en tydlig knapp. En pausad portal används och kontrolleras inte. En arkiverad portal
  göms ur listan men finns kvar (*Visa arkiverade* → *Ta tillbaka*) och används eller kontrolleras aldrig.
- **Tittande (ny flik):** tittad tid per dag med stapeldiagram, uppdelat per box och per profil, och **uppskattad datamängd** (tid × bitrate,
  som du kan ändra: live 4, film 6, serie 4 Mbit/s). Det är en uppskattning, källan rapporterar ingen verklig trafik. Underlaget sparas i 30 dagar.
  Under finns **historik per profil** (titel, tid, längd; välj profil och dag). Vuxenläget räknas bara som tid under *Övrigt*, aldrig med titlar
  och aldrig i historiken. Gäst är en egen rad utan historik. Inspelningar räknas inte.
- **Varning när abonnemanget går ut:** under Boxar → Avbrottsnotiser (3, 7, 14 eller 30 dagar innan, standard 7; högst en notis per dygn).
- **Rapport och TV4 Nyheterna: du väljer sändning.** Trycker du visas de två senaste huvudsändningarna (längsta sändningen med namnet varje dag
  ur programguiden, alltså inga fasta tider) med dag och klockslag. Har kanalen inget arkiv visas "Kanalen har inget arkiv" (eller så spelas
  den direkt om den sänds nu). De förinlagda Rapport och TV4 Nyheterna byts automatiskt en gång. Kräver ny app på boxarna för valdialogen; äldre
  appar spelar den senaste direkt.
- **Visa som (ny flik):** välj profil och box och se Hem som den profilen ser det (Mina program, Fortsätt titta, favoriter, grupper och synliga
  kategorier med antal), med samma regler som i appen. Skrivskyddat, en förenklad bild och inte en exakt skärmdump. Vuxenläget visas aldrig.
- Innehåll från 2.9.6 ingår.

## 2.9.6

- **Mina program väljer rätt kanal:** förslagen tog i 2.9.5 ibland fel kanal, så att TV4 Nyheterna och Robinson hamnade på CCTV4. Nu måste
  hela kanalnamnet stämma (landsprefix som SE: och ord som HD räknas bort), så CCTV4, TV4 Film och liknande väljs aldrig.
  Förslag som redan hamnat på CCTV flyttas automatiskt till TV4 en gång.
- **Aktuellt** föreslås bara om SVT2 har arkiv, eftersom den annars bara kan spelas direkt. Redan inlagd Aktuellt tas inte bort.
- Innehåll från 2.9.5 ingår.

## 2.9.5

- **Hemfix (webbadmin → Boxar):** dra in zipfilen för en ny version så uppdateras tillägget utan GitHub. Filerna kontrolleras
  (bara tillåtna filtyper och sökvägar), syntaxkontrolleras och provas innan de används, och tillägget startar sedan om sig självt.
  En hemfix som inte håller sig igång tas bort vid nästa start, och installerar du en nyare version av tillägget på vanligt sätt tas
  den bort av sig själv. Gäller bara tillägget och webbadminen, **inte appen på boxarna**. Första gången krävs den vanliga
  uppdateringen via GitHub, eftersom startskriptet måste ändras.
- **Mina program (Rapport, Aktuellt, TV4 Nyheterna, Robinson):** läggs in automatiskt en gång för varje profil som inte har några,
  om kanalerna finns (har du tagit bort dem kommer de inte tillbaka). Aktuellt är nytt. I den enkla menyn finns nu
  *Nyheter och Mina program* under Sök; på Hem och telefonen ligger de överst som förut.
- **Sök på TV:n:** tangentbordet fälls in när du går till träffarna, så att de syns. *Vänster* från första träffen tar tillbaka det.
  Sena träffar från programguiden tappar inte längre markören, och sökningen visar *Söker…*. I den enkla menyn följer
  tangentbordet markören på samma sätt.
- **Golf och Ridsport:** under *Sport* i webbadminen visas vilka av dina kanaler som sänder sporten (Viaplay/V Sport Golf, Golf Channel,
  Eurosport, Horse & Country, FEI TV med flera, utifrån vilka som har rättigheterna i Sverige). Du kryssar i och trycker *Skapa grupp*:
  gruppen skapas, sökorden för sändningar utökas och nya filmer och serier om sporten bevakas. Programguiden läser nu även de kanaler som
  bara ibland sänder sporten (Eurosport, TV4 Sportkanalen, SVT m.fl.), vilket är varför Ridsport förut bara hittade beIN.
- Innehåll från 2.9.4 ingår.

## 2.9.4

- **Chromecast:** startar inte filmen inom 15 sekunder görs ett andra försök med filtypen MP4 (Googles mottagare känner ofta igen
  innehållet ändå). Lyckas inte det heller får du ett tydligt meddelande och uppspelningen fortsätter på telefonen. Resultatet
  syns i webbadminen under *Diagnostik* som *Chromecast* (enhet, filformat, svar), så att orsaken går att läsa av.
- **Serier på TV:n:** upp-pilen tar dig nu från avsnittslistan upp till säsongsflikarna.
- **Sedda avsnitt:** håll in OK på ett avsnitt för *Markera som sett/osett* och för hela säsonger. Ett avsnitt räknas som sett vid
  85 % (förut 92 %). Delvis sedda avsnitt visar en stapel, t.ex. ▰▰▰▱▱ 60 %. Gästprofilen sparar fortfarande inget.
- Innehåll från 2.9.3 ingår.

## 2.9.3

- **Ljud och undertext i spelaren på TV:n:** när tidslinjen syns på en film, tryck **ner** en gång till, så markeras knapparna
  *Ljud* och *Undertext* (vänster/höger byter, OK öppnar listan, upp/ner lämnar). Fortfarande även via ☰ Meny.
- **Undertexten är mindre som standard:** 70 % av spelarens standardstorlek. Ändra under *Val → Undertextens storlek* (50, 70, 100 eller 130 %).
  Egna storlekar i filen (t.ex. ASS) används inte längre.
- **Filmkväll på TV:n:** ner och upp går nu alltid raden vidare (vem tittar → filter → förslag), och förslagsraden ligger högre så att
  posterna inte klipps vid skärmkanten.
- Innehåller allt från 2.9.2.

## 2.9.2

- **Favoriter per profil i webbadmin** (*Favoriter och grupper*): välj profil överst, och använd *Visa för…* på en favorit för att den ska
  finnas hos alla eller valda profiler. *Kopiera alla till…* fyller på en annan profil (befintliga favoriter behålls).
  Vuxenfavoriter, favoritavsnitt och gäster ingår inte.
- Innehåller allt från 2.9.1.

## 2.9.1

- **Inte i Alla:** per kategori i *Kategorier* kan du kryssa i *Inte i Alla*. Kategorin (t.ex. sport) räknas då inte in i *Alla* och
  *Nytt tillagt* (inte heller på Hem eller i Nytt per dag), men har kvar sin egen flik och finns i sök.
- **Fliken Sport** i Filmer och Serier (TV och telefon) samlar alla kategorier du markerat, så att sporten går att nå utan att leta.
- **Förslag på sportkategorier:** kategorier med ord som Sport, Replay, Events, NFL, UFC eller Formula föreslås. Du bekräftar med
  *Markera föreslagna sportkategorier…*. Inget markeras utan att du säger till.
- **Valfria filmfilter** (av som standard): *Dölj sportreprisor i Alla och Nytt tillagt* (titlar som börjar med klockslag och innehåller "vs")
  och *Nytt tillagt: bara titlar med affisch*.
- **Kategorier per profil:** välj en profil i *Kategorier → Visa och ordna för* och dölj kategorier eller ändra ordningen bara för den profilen
  (t.ex. bara Kids Movies och Disney för barnprofilen). Titlarna i dolda kategorier syns inte heller i Alla för profilen.
- **Kom ihåg senaste fliken** (inställning i appen, av som standard): Filmer och Serier öppnar där du senast var i stället för startkategorin.
- Säkerhetskopian innehåller de nya valen. Databasversion 15.
- Innehåller allt från 2.9.0.

## 2.9.0

- **Efterhandsuppspelning (TV-guidens historik):** tillägget provar olika adressformer och starttider mot leverantören (sökväg och
  `timeshift.php`; källans tid, UTC och svensk tid) och sparar den som ger video. Första uppspelningen kan dröja upp till ca 10 sekunder.
  Resultatet syns i Diagnostik som *Efterhandsadress*. Ger leverantören ändå tomt svar går det inte att åtgärda härifrån.
- **Inspelningar:** vuxeninspelningar syns bara i det djupaste läget (vuxenläge med historik/favoriter på).
- **Fler sorteringar** i Filmer och Serier: A–Ö/Ö–A, nyast/äldst tillagd, betyg, år (nyast/äldst), längd (filmer) och *Med affisch först*.
- **Mina media:** *Dölj mappen helt (även undermappar)* (borta i alla lägen, ångra under *Mer… → Dolda mappar*);
  *Alla videor här* i en mapp och som favorit (öppnas direkt med vald sortering); sortering åt båda håll på namn, datum, storlek och längd
  som sparas; sök på filnamn (*Mer… → Sök i Mina media*).
- **Nästa avsnitt-rutan** visas inte längre som standard (inställning *Ruta för nästa avsnitt*). Nästa avsnitt/fil startar ändå av sig självt.
- **Avbrottsnotiser** till telefonen (leverantören svarar inte, inspelning misslyckades, box som tystnat). Webbadmin: *Boxar → Avbrottsnotiser*.
  Använder samma notify-tjänst som påminnelserna. Aldrig titlar på vuxeninnehåll.
- **Säkerhetskopia av inställningar** (profiler, snabbval, kategorier, sport, boxinställningar) som fil. Inga koder, nycklar eller inloggningar ingår.
- **Boxöversikt:** uppkopplad/senast sedd och *ny version finns* för varje box.
- Innehåller allt från 2.8.0.

## 2.8.0

- **Menyn på TV:n** klipps inte längre överst, så profilbrickan (byt användare) går att nå. När profilen saknar Mina program visas en ledtext på Hem.
- **Mina media:** sortera på *Storlek* och *Längd*, och *Visa storlek och längd* under *Visa*.
- **Handlingstexten** på serie- och filmsidan kan fällas ut: tryck (telefon) eller OK (TV) på texten när den är avklippt.
- **Spelaren hänger sig inte längre tyst:** utan bild efter 25 sekunder avbryts uppspelningen med ett felmeddelande.
  Håll in Tillbaka i 3 sekunder för att tvinga stängning.
- **Efterhandsuppspelning som misslyckas:** när boxen inte kan läsa strömmen undersöker tillägget vad källan svarar och visar det i Diagnostik
  (*Källans svar*). Inget portalbyte görs för det felet, och boxen visar ett tydligare meddelande.
- **Diagnostik:** sök och filter på box, händelse och tid, samt ett val för att dölja *Startade*.
- Hemskärmens text i det dolda läget är neutral, och de dolda gesterna beskrivs inte längre i dokumentationen (förrådet är publikt).
- Innehåller allt från 2.7.0 (revisionsåtgärderna) och 2.6.2.

## 2.7.0

Åtgärder efter en oberoende revision (fynd F01–F17). Allt har egna tester (468 kontroller).

- **Fjärråtkomst bara för telefonen:** Home Assistant-nyckeln lämnas bara ut till enheter du kryssat i under *Boxar*.
  **Efter uppdatering:** kryssa i *Får hämta fjärråtkomst* för telefonen, annars tappar den fjärråtkomst. (F02)
- Webbadminen släpper inte längre in någon när Home Assistant inte kan bekräfta vem det är. (F03)
- Mätvärden från boxarna saneras innan de visas (stoppar HTML-injektion i webbadminen). (F01)
- Vuxentitlar visas aldrig i översikten, sensorn *TV Hub status*, konfliktmeddelanden eller schemat i vanligt läge.
  Programguiden för vuxenkanaler är tom i vanligt läge. En inspelning får bara hämta en ström när den är aktuell. (F05)
- Bara kanaler, filmer och serier som finns i listan kan spelas. (F05)
- Två boxar som startar samtidigt kan inte båda få ström. Inspelningar räknas fortfarande inte som egen ström. (F06)
- Parningskod kan bara användas en gång, också vid parallella försök. Spärr mot gissning av parningskoder och PIN
  (också profil-PIN). (F07, F04)
- Bildproxyn hämtar inte adresser i hemnätet eller via omdirigeringar. Bildcachen har en storleksgräns i byte. (F09)
- Gränser: trådar, negativa längder, tidsgräns för tysta anslutningar, storlek på svar från källan. Admin-API:t kräver JSON. (F10, F16)
- Appnedladdning: ett lås och en unik temporärfil per nedladdning. (F11)
- GitHub-bygget: appen publiceras bara om backend-testerna går igenom och signeringsnyckeln finns; minimala rättigheter. (F12)
- Android: filer raderas bara i inspelningsmapparna, VLC får bara filer på USB/lagring, favoriter från NAS som sparats i vuxenläget
  är dolda i vanligt läge, och avparning tar också bort fjärrnyckeln. (F13, F15, F17)
- AI-underlaget innehåller inte profilens namn. Borttagen profil tar också bort sett-logg, önskelista och sessionshistorik. (F14, F17)
- Några punkter är medvetet kvar eller kommer senare; de hanteras i projektets egna anteckningar.

## 2.6.2

- **Vuxenläget stängs efter en tid du väljer, per box.** Webbadminen, Boxar: *Vuxenläget stängs efter* (5 min till 3 h
  utan knapptryck, standard 15 minuter). Gränsen på högst 3 timmar totalt, omstart och profilbyte gäller som förut.
  Boxarna hämtar tiden när hemskärmen laddas. Kräver databasversion 13 (läggs till automatiskt).

## 2.6.1

- Inga namn på personer eller lokala lag i koden längre (förrådet är publikt). Profiler och lag läggs till i webbadminen.
  Den som redan uppdaterat till 2.5/2.6 påverkas inte: profilerna och lagen finns kvar.

## 2.6.0

- **Mina sporter:** Sport i menyn och raden *Sport: nu och snart* på Hem. Just nu, Kommande och I efterhand
  (ur arkivet) för sporter och lag du väljer, t.ex. golf, ridsport och fotbollslandslagen. Sporter, lag och
  sökord ändras i webbadminen under den nya fliken **Sport**.
- **Fråga när matchen börjar:** boxarna frågar "Byt kanal?" och telefonen får en notis, för de sporter och lag du väljer.
- **Spela in lagets matcher** automatiskt på vald box.
- **Röststart:** "matchen som sänds nu" som nytt röstkommando.
- **Sensorer i Home Assistant:** nästa match, när den börjar och om en match pågår.
- **När går …?** Sök på TV:n och i webbadminen letar även i programguiden: sänds nu, kommande och i efterhand.
- **TV-guiden:** alla kanaler i en grupp på en tidslinje. OK spelar, ser i efterhand eller påminner.
- **Ny spelarlist för live:** programmets namn, minuter kvar, förlopp, nästa program och klockan.
- Menyn till vänster rullar om den inte får plats på höjden.
- Webbadminen: fält som inte gäller (t.ex. Titel och Kanal i röstkommandon) döljs nu som de ska.

## 2.5.0

- **Profiler:** en per person, plus Tillsammans och Gäst (läggs till, ändras och tas bort i webbadminen under *Profiler*).
  - Egna favoriter, Fortsätt titta, sedda avsnitt och snabbval per profil. Befintliga favoriter hör till den första profilen.
  - Byt profil med den runda bilden överst i menyn (TV) eller uppe till höger (telefon). Valfri kod per profil.
  - Gäst sparar ingenting. Bara profiler med *Får använda vuxenläget* kan slå på vuxenläget.
  - Tittarlägena i Filmkväll är samma sak som profilerna.
- **Per box** (webbadminen, *Boxar*): startprofil, tillbaka till startprofilen efter X timmar utan användning,
  och om koden för vuxenläget krävs. En box med "vardagsrum" i namnet: Tillsammans, 4 timmar, kod. "Sovrum": första profilen, ingen kod.
- **Mina program:** snabbval överst på Hem och under Favoriter, t.ex. senaste Rapport, senaste huvudsändningen av
  TV4 Nyheterna (19:00/22:00) och nästa avsnitt av Robinson ur TV4:s arkiv. Fortsätter där du var.
- **Snyggare och tydligare:** sidan bakom menyn tonas ner när menyn fälls ut, grå platshållare medan listor hämtas
  i stället för "Hämtar…", och begripliga felmeddelanden i stället för felkoder.
- Röstkommandon i webbadminen: kanalsökningen fungerade inte (listan med kanaler blev tom). Rättat.

## 2.4.0

- **USB-disken syns bara i vuxenläget** (i Mina media). I vanligt läge visas den inte alls, och ingen ruta visas när den sätts i.
  Inspelningar påverkas inte.
- **Favoriter från Mina media:** håll in OK på en mapp, video eller bild → ☆ Lägg till i favoriter. Under Favoriter finns
  grupperna **NAS** och **USB-disk**. Favoriterna sparas bara på boxen. USB-favoriter syns bara i vuxenläget med historik påslagen.

## 2.3.0

- **Filer boxen inte kan avkoda** (t.ex. gamla DivX/Xvid-filmer) i Mina media öppnas automatiskt i VLC-appen, om den finns
  på boxen. Nästa gång öppnas samma fil direkt i VLC. Utan VLC visar felrutan filens format och föreslår VLC.
- **Favorit under uppspelning:** "☆ Lägg till kanalen i favoriter" / "★ Ta bort …" i spelarens meny (OK två gånger
  på TV:n, Mer på telefonen). Gäller kanaler, filmer och serier (för avsnitt).
- **Alla videor, nyast först** i Mina media: videorna i en mapp och alla dess undermappar i en lista, i datumordning.
  Under Sortera, eller håll in OK på en mapp. Datum läses ur filnamnet (VID_20190612…, 2019-06-12 …) när det finns.

## 2.2.0

- **Mina media** på boxarna: filmer, hemvideo och bilder från NAS:en (DLNA, t.ex. Synologys Media Server) och från en USB-disk.
  - NAS:en hittas automatiskt, eller läggs till med IP-adress. USB-disken dyker upp när den sätts i.
  - Mappar som på disken, med Sortera (namn/datum) och Visa (affisch, lista, bara bild).
  - Filmer fortsätter där du slutade och nästa fil i mappen spelas automatiskt. `.srt` bredvid filmen läses in.
  - Bilder med bildspel. Mobilfoton vänds rätt.
  - Inget visas i Fortsätt titta på Hem. Dolda mappar och mappar med XXX/18+/Adult i namnet syns bara i vuxenläget.
- Tillägget byggs inte längre för armv7 (varningen i Home Assistant försvinner).

## 2.1.0

- **Favoriter** som eget val i menyn på TV:n, och gruppen ★ Favoriter i Filmer och Serier.
- **Visa:** affisch med titel (standard), lista med mer info, eller bara bild. Titlar under affischerna även på Hem.
- **Röstsök:** 🎤 i Sök på TV:n och telefonen öppnar Googles röstinmatning.
- **Nytt per dag** under Inställningar: per dag, kategorierna med nya titlar och antal.
- **Påminnelser:** "Påminn mig" i programguiden. Boxen frågar om den ska byta kanal, och telefonen kan få en notis via Home Assistant.
- **Insomningstimer** i spelaren.
- **Starta på senaste kanalen** (inställning per box).
- **PIN-kod för vuxenläget** (valfri, sätts i webbadminen).
- **Ta bort från Fortsätt titta:** håll in OK på kortet.
- **Snabbare start:** Hem från förra gången visas direkt och uppdateras i bakgrunden.
- **Navigering på TV:n:**
  - Upp från grupperna i Filmer och Serier går till Sortera, Visa och Filter.
  - Vänster längst till vänster går till menyvalet för sidan du är på.
  - Hem rullar utan att hoppa.
  - Tillbaka går till Hem.
  - "Mer som detta" på filmsidan går att nå.
- **Vuxeninspelningar:** dold mapp med neutrala filnamn, neutral notis, dolda i webbadminen tills "Visa 18+" är ikryssat.
  Händelseloggen visar inte titlar på vuxeninnehåll.

## 2.0.0

- **Röststyrning och fjärrstart.** Säg till Google "Fortsätt titta i sovrummet", "Spela senaste Rapport i
  vardagsrummet" eller "Pausa vardagsrummet". Kommandona skapas i webbadminen (*Boxar → Röststyrning och
  fjärrstart*) och blir skript i Home Assistant som kan slå på TV:n och byta källa först.
  - Telefonen: *📺 Spela på TV:n* på filmer, serier, avsnitt och kanaler.
  - Webbadminen: *Spela på box* i Biblioteket.
  - Boxen lyssnar i bakgrunden. Z10 Pro behöver behörigheten "Visa ovanpå andra appar" för att starta när
    appen är stängd (Inställningar → Röststyrning när appen är stängd).
- **Fjärrkontrollens knappar:** Meny, ★, färgknapparna, Info, Guide, Inspelning och siffror för
  kanalnummer. Lär in andra knappar under Inställningar → Fjärrkontrollens knappar.
- **Chromecast från telefonen** för filmer och avsnitt (MP4; MKV på vissa enheter).
- **Bild-i-bild** på telefonen: hemknappen under uppspelning.
- **Kategorier:** egna visningsnamn och startkategori (t.ex. "Premiere Cinemas") i webbadminen, *Kategorier*.
  Gäller både TV:n och telefonen.
- **Telefonen:**
  - Grupper väljs i en sökbar lista i stället för att svepa.
  - *Nytt tillagt* med datum (Idag, Igår, 18 okt) för filmer, serier och kanaler.
  - *Visa alla* på raderna på Hem (tryck på radens rubrik).
- Mobilläge kan väljas även i det klassiska utseendet.
- *Sök efter uppdatering* i appen frågar GitHub direkt i stället för att vänta upp till 30 minuter.

## 1.9.0

- Filmkväll: välj vem som tittar (Själv, Med döttrarna, Med vuxen eller egna) och få tio förslag
  med en kort motivering. Finns i menyn på TV:n, överst i telefonen och i webbadminen.
  - Snabbval: Film/Serie, Barnvänligt, Animerat (med/bara/inget), Kortare än 2 h, Bara nytt,
    Högt betyg och Svenskt.
  - På varje förslag: Spela, Detaljer, Redan sett, Inte i kväll, Aldrig och Vill se. Listan fylls på direkt.
- Tummar (👍👍 Älskade, 👍 Bra, 👎 Inte för oss) per tittarläge. Efter en film frågar appen
  "Vad tyckte ni?" (och "Vem tittade?" om inget tittarläge är valt).
- Logg över vad ni sett, med datum och tittarläge. Filmer som setts klart loggas automatiskt.
- Vill se-lista. Titlarna kommer först i Filmkväll.
- Samma film i flera versioner (4K, svensk text, olika kategorier) visas bara en gång. Detaljsidan
  har knappen Versioner. Webbadminen: välj om svensk text eller högsta kvalitet ska visas först.
- Titta igen: per tittarläge kan älskade titlar föreslås igen efter ett antal dagar (Med döttrarna: 90).
- Import från IMDb och Letterboxd (CSV) i webbadminen.
- AI (valfritt, utbytbart): OpenAI, Anthropic (Claude) eller egen server som Ollama. Räknar om
  förslagen varje natt kl 04 och svarar på frågor i webbadminen. Utan AI-tjänst kan underlaget
  kopieras till valfri AI-chatt och svaret klistras tillbaka.
- Vuxeninnehåll loggas, betygsätts och föreslås aldrig i Filmkväll.

## 1.8.1

- Rättat: tillägget fick inte sin nyckel till Home Assistant. Därför fungerade inte "Testa vägen"
  (ingress-adressen saknades) och inte heller notiserna från Bevakningar.
- Appen: markerade menyval och rader visar nu mörk text på den vita markeringen (var vitt på vitt).
- Appen: i sökens tangentbord går vänster i första kolumnen till menyn och höger i sista kolumnen
  till träffarna.

## 1.8.0

- Appen fungerar på telefonen (Android), i samma app som på boxarna:
  - Mobilläge väljs automatiskt på en telefon: stående, touch och flikar längst ner
    (Hem, Live, Filmer, Serier, Sök). Inställningar uppe till höger.
  - Spelaren: tryck på bilden för knappar (paus, −10/+30 s, kanal upp/ner, ljud, undertexter),
    dra i reglaget för att spola och vrid telefonen för helskärm.
  - Dolt vuxenläge på telefonen (gesten beskrivs inte här). I vuxenläget: ett dolt val
    för att visa eller dölja favoriter och historik.
  - Inspelning görs fortfarande på boxarna och visas inte på telefonen.
- Utanför hemmet via Nabu Casa, utan att öppna något i routern:
  - Ställs in i webbadminen under Boxar → Telefon utanför hemmet, med knappen Testa vägen.
  - Telefonen byter själv: hemadressen på hemmets wifi, Nabu Casa annars.
  - Videon går direkt från Toucan till telefonen.
- Säkerhet: vanliga (icke-admin) användare i Home Assistant får nu öppna tillägget, eftersom appen
  behöver det. Webbadminen spärras ändå för alla som inte är administratörer.

## 1.7.0

- Spela in filmer och serier till USB-minnet i boxen (nedladdning från Toucan):
  - Film: knappen "⬇ Spela in" på detaljsidan, eller håll in OK på filmen.
  - Serie: "Spela in hela serien" på seriesidan, eller håll in OK på ett avsnitt för avsnittet eller säsongen.
  - Webbadminen: välj box i detaljrutan i Biblioteket.
- Nedladdningar görs en i taget och fortsätter där de slutade om strömmen bryts. En avbruten film raderas.
- Inspelningarna spelas upp under Inspelningar, med spolning och "fortsätt där du var".
- Favoritavsnitt: håll in OK på ett avsnitt och välj "Lägg till i favoriter". Avsnittet syns bland
  favoriterna och spelas direkt där du slutade. Favoritavsnitt markeras med ★ på seriesidan.

## 1.6.1

- "Nytt tillagt" och raderna Nytt: filmer/serier visar bara titlar som dykt upp i listan sedan
  TV Hub började hålla koll, med det datum de dök upp. Källans egna datum används inte längre där.
- Webbadminen: sorteringen "Nytt tillagt (TV Hubs datum)" i Biblioteket.

## 1.6.0

- Nytt modernt utseende i boxarna, inspirerat av Apple TV, Netflix och Google TV:
  - Smal meny med ikoner till vänster som fälls ut med texter.
  - Hem med stor bild överst och rader: Fortsätt titta, favoritkanaler med nu-program,
    nytt, topprankat, grupper och sparade filter.
  - Live-TV med kanalloggor, nu-program med förlopp och de närmaste programmen.
  - Filmer och Serier som affischrutnät med kategorier som flikar, sortering och filter.
  - Sök med eget bokstavsrutnät och senaste sökningar.
  - Detaljsida med bakgrundsbild och "Mer som detta", seriesida med säsongsflikar och avsnittsbilder.
  - Spelare med tidslinje, sluttid, LIVE-märke, kanallogga och "Nästa avsnitt".
  - Sidopaneler i stället för dialogrutor, vit markering och typsnittet Inter.
- Det klassiska utseendet finns kvar: Inställningar → Utseende.
- Ny appikon och banner.

## 1.5.0

- Vuxeninnehåll avgörs nu bara av kategorin ("For Adults", "Porn" eller "XXX" i namnet). Vanliga filmer
  som *xXx* eller *Hustlers* syns i vanligt läge igen. Kategorier kan markeras manuellt under Kategorier.
- Vuxenläget: dolt val som visar eller döljer favoriter, Fortsätt
  titta, senaste kanaler och sedda. Läget startar alltid med dem dolda. Tittandet sparas ändå.
- Spola i filmer, avsnitt, inspelningar och tidigare program: vänster 10 s bakåt, höger 30 s framåt.
  Håll in för att spola snabbare.
- Tillbaka två gånger i menyn för att stänga appen.
- "Nya filmer" heter nu "Nytt tillagt" och visar filmer, serier och kanaler med datum.
  Nya titlar får TV Hubs eget datum från och med nu.
- Manuell inspelning med valfri längd, även för kanaler utan programguide (i boxen och i webbadminen).
- Tydligare varningar: alltid med boxens namn, på svenska och sammanslagna när samma fel upprepas.

## 1.4.1

- Biblioteket: kategorilistan följer valet under Innehåll. Vuxenkategorier visas bara med
  "Bara 18+" eller "Allt", och valet Innehåll finns nu även för kanaler.

## 1.4.0

- Nytt bibliotek i webbadminen: samma sortering och filter som i boxen (betyg, längd, årtal, genre,
  senast tillagda) och sök inom en kategori.
- Tre visningar: lista, små och stora affischer. Valet sparas.
- Klicka på en titel för detaljer: affisch, handling, regi, skådespelare, avsnitt för serier,
  favorit och grupp. Från en kanal kan man gå direkt till inspelning.
- Välj innehåll: vanligt, bara 18+ eller allt.
- Affischerna hämtas via Home Assistant, så att de syns även via Nabu Casa (https).

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
