# Ändringar

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
  - Dolt vuxenläge: tryck tre gånger snabbt på "TV Hub" överst. I vuxenläget: håll in "TV Hub"
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
- Vuxenläget: dolt val (höger tre gånger på Inställningar) som visar eller döljer favoriter, Fortsätt
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
