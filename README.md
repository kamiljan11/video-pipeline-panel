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
| Oś | V1 ujęcia (kolor = zoom: statyczny / na cięciu / drift / najazd / ręczny), V2 grafiki, V3 slajdy+PiP, A1 fala głosu, A2 muzyka, A3 SFX, znaczniki krytyka (C1–C9: kody z `critic.py`). Kółko = zoom osi, przeciągnięcie grafiki = przesunięcie (zaczepia się do najbliższego słowa + przesunięcie). |
| Transkrypcja | klik = wytnij / przywróć słowo; Alt+klik = najazd; Shift+klik = zaczep zaznaczoną grafikę. Klawiatura: strzałki po słowach, Enter / Alt+Enter / Shift+Enter. Najazd podkreślony falą = nie widać go w filmie (limit zoomu `zmax`, ramka grafiki albo początek ujęcia). |
| Inspektor | ujęcie: zoom a/b/t0/t1 (max `zmax` z face_track) + klatka z kadrem i głową; najazd: siła, czas, usuń; grafika: tekst, podtytuł, ikona Lucide, typ, długość, przesunięcie, usuń / duplikuj / nowa. |
| Ustawienia filmu | drift, zoomy na cięciach, ogon, pasek postępu, hook_text, głośność muzyki i SFX, seed muzyki. |
| Podgląd ±10 s | render 360p (`assemble.py --proxy`) wokół znacznika, do `render/panel/preview/`. Każdy podgląd startuje wyciszony (dźwięk po kliknięciu głośnika). Pełnego renderu nie zmienia. W trakcie podglądu zmiany grafik czekają (render grafik czyta opis raz). |
| Krytyk | odświeżany po każdej zmianie (przeliczenie = build_edl + face_track + critic). „Popraw automatycznie” = `critic.py --fix` na kopii planu i diff do akceptacji; „Akceptuję” = wyjątek z powodem (`critic_accept`), dopasowany do **dokładnego** tekstu uwagi: wiele uwag ma w treści czasy (C1, C3, C4), więc po edycji, która je przesunie, uwaga wraca jako nowa i trzeba ją zaakceptować jeszcze raz (stary wyjątek zostaje w planie, nic nie robi). Waga uwagi jest podana słowem i kolorem. Uwagi i znaczniki grafik w tekście: Tab + Enter. |
| Render | `run_v4.sh` w kolejce, log na żywo (wraca po odświeżeniu strony), ostrzeżenie przy baterii < 40 % bez ładowania. Stop (z potwierdzeniem) zatrzymuje zadania panelu; przy renderze też `stop_render.py`, który kończy **każdy** render pipeline'u na laptopie. W trakcie renderu (także uruchomionego z terminala dla tego projektu, ścieżką pełną albo względną) panel nie zapisuje zmian, a przy renderze spoza panelu nie uruchamia też Sprawdź / Podgląd / Popraw (przeliczają EDL, który czyta render). |
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
| „przekroczył limit … s” | skrypt wisiał (ffmpeg/Chrome); limity: przeliczenie 10–30 min, podgląd 15 min, render 4 h. Proces ignorujący SIGTERM dostaje SIGKILL po 10 s; kolejka nie czeka na wnuki, które odłączyły się od grupy |
| „nie ma takiej ikony” / „lista: dodaj punkt” | `gfx_specs.json` jest wspólny dla filmów, więc panel sprawdza każde pole tak, jak używa go `gfx/render.mjs` (ikona = nazwa z `node_modules/lucide-static/icons`, lista = co najmniej 1 punkt) |
| „zmienił się poza panelem” | plan zmienił pełny render albo inna sesja; odśwież stronę, historia od tego miejsca |
| „poprzednie uruchomienie się nie skończyło: usuwam …” | Stop, limit albo awaria przerwały podgląd / grafiki / render: `assemble.py` i `gfx/render.mjs` użyłyby uciętych plików (`segs/*.mkv`, `gfx/<id>.mov`). Znacznik (`unfinished.json` w katalogu podglądu; `gfx.unfinished.json` w `tmp` projektu — grafiki są projektu; `render/<film>/panel-unfinished.json` — ujęcia `render/<film>/segs` są wspólne dla projektów z filmem o tej nazwie) znika dopiero po udanym przebiegu; następny start usuwa ujęcia zapisane w przerwanym przebiegu i odkłada jego grafiki do `stale-gfx`. Stop już po złożeniu filmu (np. w końcowym krytyku) też liczy się jako przerwany: następny render składa te ujęcia od nowa (dłużej, ale bez ryzyka — `render.log` jest wspólny dla projektów, więc wpis `DONE` nie dowodzi, który przebieg skończył). Render spoza panelu (terminal) znacznika nie zostawia |
| „render się nie udał (brak „DONE” w render.log)” | `run_v4.sh` kończy się kodem 0 także po błędzie `assemble.py`; panel uznaje render za udany tylko po jego wpisie `DONE <film>`. Przyczyna w logu zadania |
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
| `critic_accept` | `[{"code","msg","reason"}]` | `critic.py`: nie zgłasza i nie poprawia automatycznie (także C6 w `--fix`) |

Kontrakty skryptów używane przez panel (bez nich skrypty działają jak przed panelem):

| Skrypt | Wejście | Wynik |
|---|---|---|
| `critic.py plan.json --json <plik>` | flaga | `{"issues": [{code, sev, msg, at?, end?}], "fixes": [...], "accepted": [...], "total": s}`; kod wyjścia 1 = są błędy (to wynik, nie awaria) |
| `assemble.py edl.json out --proxy` | flaga | szybkie kodowanie + skala 360p; bez flagi komendy ffmpeg identyczne |
| `assemble.py` | EDL `music_offset` (s) | `-ss` przed muzyką i SFX: podgląd wycinka zaczyna podkład od środka (pisze go tylko `preview.py`) |

Nowe ustawienie filmu: jedna linia w `GLOBALS` (`montazownia/planops.py`, z walidacją) + jedno pole w `renderGlobals` (`ui/app.js`);
jeśli czyta je pipeline, zmiana w skrypcie + test w `tests/test_pipeline_compat.py`.

## Testy

```bash
cd ~/video-pipeline/panel && .venv/bin/python -m unittest discover -s tests -v
```

`test_integration.py` działa na kopii projektu 2026-10-20 w katalogu tymczasowym (~2 min, prawdziwe ffmpeg/Chrome); `MONTAZ_SLOW=0` go pomija.
`test_pipeline_compat.py` porównuje obecne skrypty z wersją sprzed panelu w `tests/pipeline_baseline/*.py.orig` (w gicie panelu,
bo `~/video-pipeline` nie jest w gicie; `.orig`, żeby linter nie czytał starego kodu); brak plików = test **pada**. To też droga
cofnięcia zmian w `build_edl.py`/`critic.py`/`assemble.py` (`cp tests/pipeline_baseline/critic.py.orig ../critic.py`); pełny snapshot
pipeline'u: `render/pipeline-snapshot-20261020-200753.tar.gz`. Systemowy `python3` (bez venv panelu) pomija testy HTTP.

## Architektura

Rdzeń bez efektów ubocznych (`planops.py` edycje planu, `timeline.py` widok osi, `preview.py` wycinek EDL) + powłoka
(`store.py` zapis atomowy i historia, `jobs.py` kolejka jednego zadania naraz, `pipeline.py` komendy skryptów, `media.py` fala i klatki,
`server.py` FastAPI). Frontend: czysty JS + vis-timeline 8.5.4 (`vis-timeline-graph2d.min.js` z paczki npm `vis-timeline`, skopiowany do `ui/vendor`,
licencja MIT/Apache-2.0; aktualizacja = podmiana dwóch plików + przejście testów UI). Decyzje: `docs/adr/`, słownik pojęć: `docs/GLOSSARY.md`.
