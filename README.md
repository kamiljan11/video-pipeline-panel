# Montażownia — panel montażowy pipeline'u wideo

Lokalny panel w przeglądarce do poprawiania montażu, który zrobił AI-pipeline z `~/video-pipeline`.
Panel niczego nie montuje sam: zmienia pliki planu (`plan_<film>.json`, `gfx_specs.json`) i uruchamia te same
skrypty co `run_v4.sh` (`build_edl.py`, `face_track.py`, `critic.py`, `assemble.py`, `music/make_beds.py`, `gfx/render.mjs`).

## Start

```bash
montazownia ~/Videos/Montaz/2026-10-20/work
```

Otwiera `http://127.0.0.1:8790`. Opcje: `--out <katalog wyników>` (domyślnie `../out`), `--port 8790`, `--no-browser`.
Konkretny film od razu: `http://127.0.0.1:8790/?film=short3_jedno_zlecenie`.

Pierwsza instalacja (osobny venv panelu; pipeline ma swój `../.venv`):

```bash
cd ~/video-pipeline/panel && uv venv && uv pip install -r requirements.txt
ln -s ~/video-pipeline/panel/bin/montazownia ~/.local/bin/montazownia
```

## Co robi

| Element | Działanie |
|---|---|
| Oś | V1 ujęcia (kolor = zoom: statyczny / na cięciu / drift / najazd / ręczny), V2 grafiki, V3 slajdy+PiP, V4 b-roll, A1 fala głosu, A2 muzyka, A3 SFX, znaczniki krytyka (C1–C10: kody z `critic.py`; C10 = gramatyka montażu: auto-najazdy bez momentu, moment bez sugerowanej grafiki; C11 = b-roll, którego nie dało się ułożyć). Kółko = zoom osi, przeciągnięcie grafiki = przesunięcie (zaczepia się do najbliższego słowa + przesunięcie). |
| Transkrypcja | klik = wytnij / przywróć słowo; Alt+klik = najazd; Shift+klik = zaczep zaznaczoną grafikę. Klawiatura: strzałki po słowach, Enter / Alt+Enter / Shift+Enter. Najazd podkreślony falą = nie widać go w filmie (limit zoomu `zmax`, ramka grafiki albo początek ujęcia). |
| Inspektor | ujęcie: zoom a/b/t0/t1 (max `zmax` z face_track) + klatka z kadrem i głową; najazd: siła, czas, usuń; grafika: tekst, podtytuł, ikona Lucide, typ, długość, przesunięcie, usuń / duplikuj / nowa. |
| Ustawienia filmu | drift, zoomy na cięciach, ogon, pasek postępu, hook_text, głośność muzyki i SFX, seed muzyki. |
| Podgląd ±10 s | render 360p (`assemble.py --proxy`) wokół znacznika, do `render/panel/preview/`. Każdy podgląd startuje wyciszony (dźwięk po kliknięciu głośnika). Pełnego renderu nie zmienia. W trakcie podglądu zmiany grafik czekają (render grafik czyta opis raz). |
| Krytyk | odświeżany po każdej zmianie (przeliczenie = build_edl + face_track + critic). „Popraw automatycznie” = `critic.py --fix` na kopii planu i diff do akceptacji; „Akceptuję” = wyjątek z powodem (`critic_accept`), dopasowany do **dokładnego** tekstu uwagi: wiele uwag ma w treści czasy (C1, C3, C4), więc po edycji, która je przesunie, uwaga wraca jako nowa i trzeba ją zaakceptować jeszcze raz (stary wyjątek zostaje w planie, nic nie robi). Waga uwagi jest podana słowem i kolorem. Uwagi i znaczniki grafik w tekście: Tab + Enter. |
| Render | `run_v4.sh` w kolejce, log na żywo (wraca po odświeżeniu strony), ostrzeżenie przy baterii < 40 % bez ładowania. Stop (z potwierdzeniem) zatrzymuje zadania panelu; przy renderze też `stop_render.py`, który kończy **każdy** render pipeline'u na laptopie. W trakcie renderu (także uruchomionego z terminala dla tego projektu, ścieżką pełną albo względną) panel nie zapisuje zmian, a przy renderze spoza panelu nie uruchamia też Sprawdź / Podgląd / Popraw (przeliczają EDL, który czyta render). |
| Momenty | pas „Momenty” na osi: momenty montażu (hak, liczba, kontrast, puenta, lista, pytanie…) wykryte z tekstu nagrania i efekt, jaki dostały wg gramatyki montażu (`~/video-pipeline/style/grammar.json`, osobno long 16:9 i short 9:16): najazd, „odstęp” (inny najazd za blisko), „cisza” (moment poboczny, kadr i tak żyje), „ramka” (grafika z boku jest efektem), „cięcie” (słowo wycięte), „wył.”. „rytm” = najazd tylko dlatego, że kadr stał za długo (bez momentu). „Rozpoznaj momenty” = `beats.py --gfx` (reguły; lokalny model z `--llm`, gdy działa), propozycja w oknie, „Zastosuj” zapisuje przez historię (cofalne); w shortach dochodzą pionowe karty liczb. Ctrl+klik na słowie = nowy moment; inspektor: typ, wyłącz, wymuś. Lokalny model (`--llm`, w panelu `POST …/beats?llm=1`): `~/AI/ai.sh start chat` = llama.cpp w kontenerze na `127.0.0.1:8081` (adres zmienia `BEATS_LLM_URL` — tylko lokalny, bo idzie tam tekst nagrania); wyłączony albo zwraca coś dziwnego = same reguły (komunikat „llm: niedostępny” w logu zadania). |
| B-roll | „Dobierz b-roll” (tylko shorty) = przeliczenie EDL + `broll.py`: darmowe ujęcia z Pexels tam, gdzie w tekście pada konkretna rzecz z listy tematów (`grammar.json › broll.topics`), poza hakiem, grafikami i zdaniami z ważnymi momentami. Okno pokazuje klipy, pominięte miejsca i linie autorów do opisu filmu; „Zastosuj” zapisuje przez historię. Klip na pasie V4 -> inspektor -> „Usuń b-roll” (klip i słowo trafiają do `broll_rejected`: kolejny dobór ich nie weźmie; Ctrl+Z cofa). Klipy z planu, których nie dało się ułożyć, są w pustym inspektorze i w krytyku (C11). Klucz Pexels idzie z Infisicala (`~/infisical`, sekret `Pexels`) prosto do skryptu; do Pexels wychodzi tylko angielskie hasło z listy tematów (nigdy tekst nagrania). Klipy w `~/video-pipeline/render/broll/` (wspólne dla projektów, pobierane raz, ok. 10 MB/klip; katalog nie czyści się sam: usuń pliki, których nie ma w żadnym `plan_*.json › broll[].file`). Decyzja i reguły: `docs/adr/0003-broll-stock.md`. |
| Historia | kopia pliku przed każdym zapisem (`render/panel/history/<projekt>/`, ostatnie 500; stan sprzed pierwszej edycji panelu na stałe w `original/`), Ctrl+Z / Ctrl+Shift+Z. Historia jest jedna na projekt: cofnięcie może dotyczyć innego filmu (komunikat to mówi) i przelicza ten film. Cofnięcie odmawia, jeśli plik zmienił ktoś poza panelem (np. `critic --fix` w pełnym renderze) — wtedy nic nie znika ze stosu. Historia żyje do zamknięcia panelu; kopie na dysku zostają. |

## Bezpieczeństwo (kto może wołać API)

Panel uruchamia skrypty, więc przyjmuje zapytania tylko od własnych stron: serwer słucha na 127.0.0.1, nagłówek `Host` musi być
`127.0.0.1`/`localhost`, `Origin` (jeśli jest) musi być dokładnie adresem panelu, `Sec-Fetch-Site` nie może być `cross-site`/`same-site`
(inna aplikacja na innym porcie localhost, np. n8n, też nie przejdzie). Odmowy idą do logu panelu. Nazwa filmu z adresu przechodzi
dalej tylko, jeśli istnieje `plan_<nazwa>.json`, a nazwa ma wyłącznie litery, cyfry, `_` i `-` (skrypty pipeline'u wklejają ją do powłoki;
plik planu o innej nazwie, np. przyniesiony przez Syncthing, panel pomija z ostrzeżeniem w logu). Jeden panel na projekt (blokada w
`render/panel/locks/`). Brak sekretów i zewnętrznych API.

## Gdy coś nie działa

| Objaw | Co zrobić |
|---|---|
| „brak połączenia z panelem” | panel w terminalu się zamknął: uruchom `montazownia <work>` jeszcze raz. Ctrl+C i zamknięcie terminala zatrzymują zadania panelu (SIGTERM, po 10 s SIGKILL). `kill -9` panelu albo awaria — render żyje dalej; nowy panel pokaże „renderuje się poza panelem”, zatrzymasz go `python3 ~/video-pipeline/stop_render.py` |
| „port … jest zajęty” | na tym porcie działa inny panel (inny projekt): `montazownia <work> --port 8791` |
| „panel dla … już działa” | drugi panel tego projektu: otwórz pierwszy (port w jego terminalu) |
| „przekroczył limit … s” | skrypt wisiał (ffmpeg/Chrome); limity: przeliczenie 10–30 min, podgląd 15 min, dobór b-rollu 15 min (wolny Pexels: pobrane klipy zostają w `render/broll`, ponowny dobór ich nie pobiera), render 4 h. Proces ignorujący SIGTERM dostaje SIGKILL po 10 s; kolejka nie czeka na wnuki, które odłączyły się od grupy |
| „nie ma takiej ikony” / „lista: dodaj punkt” | `gfx_specs.json` jest wspólny dla filmów, więc panel sprawdza każde pole tak, jak używa go `gfx/render.mjs` (ikona = nazwa z `node_modules/lucide-static/icons`, lista = co najmniej 1 punkt) |
| `FAILED webshot <id>: …` w `gfx.log` (render) albo w wyniku zadania (podgląd) | karta `web`: zdania `find` nie ma na stronie (przepisz dokładnie, wystarczy fragment ≥ 8 liter), strona zwróciła błąd albo blokuje roboty (wtedy własny zrzut w polu `img`, README pipeline'u), adres nie jest publicznym `https://` (także nazwa wskazująca na adres lokalny). Po poprawce uruchom ponownie (nieudany zrzut nie jest zapamiętany) |
| „zmienił się poza panelem” | plan zmienił pełny render albo inna sesja; odśwież stronę, historia od tego miejsca |
| „poprzednie uruchomienie się nie skończyło: usuwam …” | Stop, limit albo awaria przerwały podgląd / grafiki / render: `assemble.py` i `gfx/render.mjs` użyłyby uciętych plików (`segs/*.mkv`, `gfx/<id>.mov`). Znacznik (`unfinished.json` w katalogu podglądu; `gfx.unfinished.json` w `tmp` projektu — grafiki są projektu; `render/<film>/panel-unfinished.json` — ujęcia `render/<film>/segs` są wspólne dla projektów z filmem o tej nazwie) znika dopiero po udanym przebiegu; następny start usuwa ujęcia zapisane w przerwanym przebiegu i odkłada jego grafiki do `stale-gfx`. Stop już po złożeniu filmu (np. w końcowym krytyku) też liczy się jako przerwany: następny render składa te ujęcia od nowa (dłużej, ale bez ryzyka — `render.log` jest wspólny dla projektów, więc wpis `DONE` nie dowodzi, który przebieg skończył). Render spoza panelu (terminal) znacznika nie zostawia |
| „run_v4.sh zakończył się kodem 1” | któryś krok filmu padł (`FAILED <krok> <film>` w `render.log`, np. `build_edl`, `face_track`, `make_beds`, `assemble`, `to_otio`; `FAILED out_name` = nazwa pliku z planu zawiera ścieżkę; `FAILED gfx` = plansze się nie wyrenderowały i żaden film projektu nie ruszył, przyczyna w `gfx.log` w katalogu `work`); nowy plik filmu nie powstaje (nic nie wychodzi ze starego EDL). Uwaga: wersja z **poprzedniego** udanego renderu zostaje w `out` pod tą samą nazwą — przed publikacją sprawdź `DONE <film>` w `render.log` albo datę pliku. Przyczyna w logu zadania |
| „render się nie udał (brak „DONE” w render.log)” | `run_v4.sh` skończył się bez wpisu `DONE <film>` (np. przerwany); panel uznaje render za udany tylko po tym wpisie |
| „Pexels: brak połączenia…” / „zły klucz API” / „limit zapytań” | dobór b-rollu przerwany po pierwszym błędzie dostawcy (film bez nowych klipów, reszta działa). Brak sieci: sprawdź internet; zły klucz: sekret `Pexels` w Infisicalu; limit: 200 zapytań/h, spróbuj później. „infisical_bridge.py zakończył się kodem 1” = padł most albo sam `broll.py` (panel podaje nazwę pierwszego skryptu z polecenia). Rozróżnij w logu zadania: `URLError`/`Connection refused`/`[BLAD API]` = Infisical nie działa albo brak sekretu `Pexels` (`cd ~/infisical && docker compose up -d`; `python3 infisical_bridge.py check`); traceback z `broll.py`/`stock.py` albo „brak EDL filmu” = błąd skryptu (najpierw „Sprawdź”). „brak klucza Pexels” = `broll.py` uruchomiony ręcznie bez mostu |
| „b-roll … pominięty: …” (C11) | klipu z planu nie dało się ułożyć: słowo wycięte, grafika w tym miejscu, plik zniknął z `render/broll` (dobierz jeszcze raz) |
| log zadania | `~/video-pipeline/render/panel/jobs/` (po zakończeniu), log serwera w terminalu panelu |

## Gdzie zapisuje

- Pliki projektu w `work/`: tylko `plan_<film>.json`, `gfx_specs.json` i to, co i tak piszą skrypty pipeline'u (`edl_*.json`, `.ass`, `gfx/*.mov`).
- Wszystko tymczasowe w `~/video-pipeline/render/panel/` (podglądy, historia, raporty krytyka, logi zadań, cache fali i klatek, podkłady muzyczne podglądu, stare grafiki); każdy podkatalog projektu ma `PROJECT.txt` ze ścieżką `work`. Przy starcie panel usuwa cache (`tmp`, `preview`, `beds`, `stale-gfx`) projektów, których `work` już nie istnieje; historii nie rusza. `MONTAZ_PANEL_DIR` przenosi ten katalog (testy używają tymczasowego). Nic tymczasowego w `~/Videos` (Syncthing); jedyny wyjątek to plik `.plan_<film>.json.panel-tmp` obok planu przez ułamek sekundy zapisu atomowego (`os.replace` w tym samym katalogu).
- Zmieniona grafika: stary `.mov` idzie do `render/panel/stale-gfx/`, żeby `render.mjs` zrobił ją od nowa.

## Klucze planu dodane przez panel

Czyta je pipeline (minimalne zmiany w `build_edl.py`, `critic.py`, `assemble.py`; bez tych kluczy wynik identyczny jak przed panelem — test `tests/test_pipeline_compat.py`).

| Klucz | Format | Kto czyta |
|---|---|---|
| `seg_zoom` | `{"<wejście ujęcia w źródle, 2 miejsca>": zoom albo {"a","b","t0","t1"}}` | `build_edl.py` (potem `face_track.py` i tak tnie do `zmax`) |
| `music_db`, `sfx_db` | liczby dB | `build_edl.py` -> EDL -> `music/make_beds.py` |
| `audio` | łańcuch filtrów ffmpeg dla głosu (zastępuje domyślny `highpass,afftdn,acompressor,loudnorm`). Nagrania odszumione już przy nagrywaniu (cisza = `-inf`): bez `afftdn`, bo drugie odszumianie zabiera górę pasma (8–12 kHz −6,6 dB) i brzmi „pod wodą” | `build_edl.py` -> EDL -> `assemble.py` |
| `steady_x` | `true` (okno ±45 s) albo liczba sekund; tylko 16:9 | `build_edl.py` -> EDL -> `face_track.py`: kadr celuje w kroczącą medianę pozycji twarzy, a nie w detekcję każdego ujęcia, i trzyma twarz w tym samym miejscu ekranu co w pełnym kadrze (`aim = A + (0.5 − A)/zoom` na otwarciu ujęcia, po przycięciu do `zmax`) — cięcie to najazd na twarz, nigdy przeskok w bok; w trakcie najazdu wewnątrz ujęcia twarz może lekko odjechać od tego miejsca. Zła wartość (np. napis) = nazwany błąd `face_track`. Ujęcie, któremu kotwica zabrałaby zoom (głowa się nie mieści), zostaje przy swojej detekcji |
| `critic_accept` | `[{"code","msg","reason"}]` | `critic.py`: nie zgłasza i nie poprawia automatycznie (także C6 w `--fix`) |
| `beats` | `[{"type","word","src"?,"text"?,"off"?,"force"?}]` — moment montażu na słowie (indeks w transkrypcji); typy i efekty w `style/grammar.json` | `build_edl.py` -> `beatfx.py` (wybór najazdów zamiast ręcznego `emph`; ręczne `emph` bez `auto` zostają), `critic.py --fix` (wymusza moment zamiast losowego najazdu), `music/make_beds.py` (SFX tylko na najazdach momentów) |
| `broll` | `[{"word","dur","lead","file","query","said","clip_dur","provider","id","author","url"}]` — klip stock od słowa-zaczepu; `file` tylko w `render/broll/` | `build_edl.py` -> `broll.place` -> EDL `broll` `[{file, at, dur, ss, word, query, id}]` + `broll_skipped`; `assemble.py` (pełny kadr pod grafikami i napisami), `critic.py` (C1, C11), `head_check.py` (pomija), panel (`preview.py`, pas V4) |
| `broll_rejected` | `[{"word","provider","id"}]` — klipy usunięte przez montażystę | `broll.py` (nie proponuje ich ani tego słowa), `build_edl.py` (EDL `broll_removed` = liczba, metryka ADR-0003 w `beatfx.py`) |
| `gfx_specs` `canvas: "v"`, `quote: true` | pionowa karta 1080x1920 w górnym pasie; `quote` = liczba wypowiedziana na głos (C9 tylko informuje) | `gfx/render.mjs`, `critic.py` (C2: karta pod hook_text = błąd) |
| `gfx_specs` `type: "web"` (`url`, `find`, `page?`, `src?` albo `img`, `box?`) | karta źródła: przytaczana strona/PDF w oknie przeglądarki, przewinięta do zdania `find`, które dostaje marker; twarz w rogu jak `slide` (ADR-0002 pipeline'u: `../docs/adr/0002-web-source-card.md`) | `gfx/webshot.mjs` (zrzut, sieć, raz), `gfx/render.mjs`, `build_edl.py` (`pip`), `head_check.py`, `make_beds.py` (whoosh), panel (edytor grafik, pas V3) |
| `gfx_specs` `sfx: [[t, "pop"\|"whoosh"\|"ding"\|"pen"], ...]` | dźwięki chwil wewnątrz karty, `t` od startu karty (pozycje listy, kreska pisaka, kluczowy fakt); nieznana nazwa = cisza | `music/make_beds.py` |
| `gfx_specs` `type: "count"` (pola jak `kw`) | ramka `kw`, w której liczby z `text` odliczają od 0 („95%”, „2,5 dnia”); panel odrzuca tekst bez liczby do odliczania (ta sama reguła co `gfx/count_lib.mjs`, test zgodności w `tests/test_planops.py`). Wycofanie: `type` -> `kw`, tekst zostaje (ADR-0005; ADR-0003 pipeline'u: `../docs/adr/0003-gsap-count-card.md`) | `gfx/render.mjs` (GSAP), `build_edl.py` (ramka: bez najazdu), `make_beds.py` (pop), panel (edytor grafik) |

Wynik w EDL (pisze `build_edl.py`, czytają `face_track.py`, `make_beds.py`, panel): `beat_fx` = `[{type, word, at, fx, gfx, sfx, broll?}]`
(raport, jaki efekt dostał każdy moment) i `zoom.keys` = `[[t, zoom], ...]`, gdy w jednym ujęciu jest kilka najazdów
(`assemble.py` składa je w jedno wyrażenie; `face_track.py` przesuwa każdy ruch pod `zmax` zamiast go spłaszczać).
Ścieżka gramatyki jest stała (`~/video-pipeline/style/grammar.json`; plan jej nie wskazuje — plany przychodzą Syncthingiem).
`grammar.json` jest sprawdzany przy wczytaniu (jeden walidator `beatfx.load_grammar`, używany też przez `beats.py` i `critic.py`:
brakujący albo zły klucz = błąd z jego nazwą); gdy `build_edl.py`, `face_track.py` albo `make_beds.py` padnie, `run_v4.sh` pisze
`FAILED <krok> <film>` do `render.log` i nie renderuje starego EDL (panel: „brak DONE”; test: `RunV4FailureTest`).
Z kluczem `beats` próg C1 krytyka = `rules.<format>.max_static` z gramatyki (bez klucza stałe 40 s / 7 s jak dawniej).
EDL zapisuje, którą gramatyką go zrobiono: `beat_grammar` = `{version, sha}` (12 znaków sha256 pliku) — treść tej wersji
zostaje w `style/history/grammar-<sha>.json` (który film cięto którymi regułami; cofnięcie edycji progu = skopiuj stamtąd).
`grammar.json` i skrypty są w lokalnym repo gita `~/video-pipeline` (`git log -p style/grammar.json` = historia reguł). Metryka z ADR: `python3 ~/video-pipeline/beatfx.py edl_<film>.json`; po każdym udanym renderze `run_v4.sh` dopisuje ją do `render/beat_stats.log`.
Sugestie `gfx`/`broll` w `beat_fx` to podpowiedzi dla montażysty (C10, inspektor momentu): z momentów automatycznie powstają
tylko karty liczb (`beats.py --gfx`, `auto_gfx`). B-roll stock dobiera osobno `broll.py` po słowach z listy tematów (ADR-0003).
Moment, którego build_edl nie wykona, ma w raporcie powód zamiast „najazdu”: `box` (ramka z boku do końca ujęcia), `manual`
(ręczny zoom ujęcia z panelu), `unknown` (typu nie ma już w gramatyce).

**Wyłączenie gramatyki dla filmu (droga cofnięcia):** usuń klucz `beats` z planu (Ctrl+Z po „Rozpoznaj momenty” albo ręcznie
w JSON-ie). Bez klucza skrypty działają jak przed gramatyką — to pilnuje `tests/test_pipeline_compat.py`, więc cofanie skryptów
nie jest potrzebne. Uwaga: pierwszy Ctrl+klik „moment” włącza gramatykę dla filmu (panel to mówi), a usunięcie ostatniego
momentu zostawia `beats: []` — gramatyka dalej działa (sam „rytm”). Kopie plików: `render/pipeline-snapshot-20261020-200753.tar.gz`
= stan **sprzed panelu** (bez `--json`, `--proxy`, `seg_zoom`: przywrócenie zepsuje panel); stanu „panel bez gramatyki” nie ma
osobno w tarze — jest w historii gita `~/video-pipeline` (`git log`). Stan z gramatyką: `render/pipeline-snapshot-20261021-121014.tar.gz`.

Kontrakty skryptów używane przez panel (bez nich skrypty działają jak przed panelem):

| Skrypt | Wejście | Wynik |
|---|---|---|
| `critic.py plan.json --json <plik>` | flaga | `{"issues": [{code, sev, msg, at?, end?}], "fixes": [...], "accepted": [...], "total": s}`; kod wyjścia 1 = są błędy (to wynik, nie awaria) |
| `assemble.py edl.json out --proxy` | flaga | szybkie kodowanie + skala 360p; bez flagi komendy ffmpeg identyczne |
| `beats.py plan.json --gfx --json <plik> [--llm]` | flaga | propozycja `{beats, gfx, specs}`; bez `--write` niczego nie zapisuje (panel stosuje ją sam, przez historię) |
| `broll.py plan.json --json <plik>` (przez `infisical_bridge.py run --secrets Pexels --`) | EDL filmu (`edl_out`) | propozycja `{broll, new, skipped, credits, format, error}`; pobiera klipy do `render/broll/`, planu nie zapisuje; `--credits` = linie autorów z planu |
| `assemble.py` | EDL `music_offset` (s) | `-ss` przed muzyką i SFX: podgląd wycinka zaczyna podkład od środka (pisze go tylko `preview.py`) |

Nowe ustawienie filmu: jedna linia w `GLOBALS` (`montazownia/planops.py`, z walidacją) + jedno pole w `renderGlobals` (`ui/app.js`);
jeśli czyta je pipeline, zmiana w skrypcie + test w `tests/test_pipeline_compat.py`.

## Testy

```bash
cd ~/video-pipeline/panel && .venv/bin/python -m unittest discover -s tests -v
```

`test_integration.py` działa na kopii projektu 2026-10-20 w katalogu tymczasowym (~2 min, prawdziwe ffmpeg/Chrome); `MONTAZ_SLOW=0` go pomija.
`test_pipeline_compat.py` porównuje obecne skrypty z wersją sprzed panelu w `tests/pipeline_baseline/*.py.orig` (w gicie panelu,
bo repo `~/video-pipeline` powstało dopiero 2026-10-21, już po panelu; `.orig`, żeby linter nie czytał starego kodu); brak plików = test **pada**. To też droga
cofnięcia zmian w `build_edl.py`/`critic.py`/`assemble.py`/`make_beds.py`/`face_track.py` (`cp tests/pipeline_baseline/critic.py.orig ../critic.py`); pełny snapshot
pipeline'u sprzed panelu: `render/pipeline-snapshot-20261020-200753.tar.gz`; z gramatyką: `render/pipeline-snapshot-20261021-121014.tar.gz`. Systemowy `python3` (bez venv panelu) pomija testy HTTP.

## Architektura

Rdzeń bez efektów ubocznych (`planops.py` edycje planu, `timeline.py` widok osi, `preview.py` wycinek EDL) + powłoka
(`store.py` zapis atomowy i historia, `jobs.py` kolejka jednego zadania naraz, `pipeline.py` komendy skryptów, `media.py` fala i klatki,
`server.py` FastAPI). Frontend: czysty JS + vis-timeline 8.5.4 (`vis-timeline-graph2d.min.js` z paczki npm `vis-timeline`, skopiowany do `ui/vendor`,
licencja MIT/Apache-2.0; aktualizacja = podmiana dwóch plików + przejście testów UI). Decyzje: `docs/adr/`, słownik pojęć: `docs/GLOSSARY.md`.
