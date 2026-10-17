# TV Hub

TV Hub gör Home Assistant till navet för din IPTV:

- **Portaler med automatiskt byte.** Lägg in Toucans adresser. TV Hub kontrollerar inloggningen varannan minut och boxarna använder alltid den som fungerar.
- **Felsökning.** När bilden hackar eller listorna inte uppdateras säger TV Hub om felet ligger hos källan, din internetleverantör, hemnätet eller kontot, och visar underlaget.
- **Bibliotek, favoriter och egna grupper.** Sök bland alla kanaler, filmer och serier från mobilen eller datorn. Allt syns direkt i boxarna.
- **Anslutningsvakt.** Toucan tillåter bara en ström åt gången. Startar du en box medan en annan tittar frågar den först och stoppar den andra innan den börjar.

## Kom igång

1. Öppna **TV Hub** i sidomenyn.
2. **Portaler:** Lägg in samma portal-URL, användarnamn och lösenord som i MyTVOnline. Klicka *Spara och testa*.
3. **Översikt:** Klicka *Uppdatera listor*. Första gången tar det upp till ett par minuter.
4. **Boxar:** Klicka *Para ny box* och skriv in koden i TV Hub-appen på boxen. Serveradressen i appen är `http://<Home Assistants IP>:8099`.

## Säkerhet

- Webbadminen nås bara genom Home Assistant (och därmed Nabu Casa med din inloggning och tvåfaktor). Den svarar inte på andra adresser.
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
