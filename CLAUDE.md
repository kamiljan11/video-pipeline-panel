# Montażownia — instrukcje dla agenta

Lokalny panel montażowy nad `~/video-pipeline` (opis: `README.md`, decyzje: `docs/adr/`, pojęcia: `docs/GLOSSARY.md`).

- Testy: `.venv/bin/python -m unittest discover -s tests` (venv panelu; systemowy python pomija testy HTTP). `MONTAZ_SLOW=0` pomija e2e.
- Lint/typy: `ruff check .`, `pyright` (konfiguracja w `pyrightconfig.json` wskazuje `.venv`).
- Panel NIE montuje: zmienia plan / gfx_specs i woła skrypty pipeline'u. Logiki montażu nie przepisujemy do panelu.
- Zmiana w `../build_edl.py`, `../critic.py`, `../assemble.py`, `../face_track.py`, `../music/make_beds.py` tylko pod nowym kluczem
  planu albo flagą, z testem w `tests/test_pipeline_compat.py` (bez klucza wynik identyczny jak `tests/pipeline_baseline/*.py.orig`).
  `../run_v4.sh` -> `RunV4FailureTest` (`tests/test_pipeline_unit.py`); `../beats.py`, `../beatfx.py`, `../style/grammar.json`
  -> `tests/test_beats.py`; `../broll.py`, `../stock.py` -> `tests/test_broll.py` (bez sieci: fałszywy dostawca). Przed edycją sprawdź mtime: w `~/video-pipeline` pracują też inne sesje.
- `~/video-pipeline` to osobne lokalne repo gita (od 2026-10-21; panel jest w nim ignorowany): zmiany skryptów i `style/`
  commituj tam, zmiany panelu tutaj.
- Nazwa filmu z HTTP -> zawsze `Panel.known()`; nazwy plików planu tylko `[A-Za-z0-9_-]` (`store.FILM_NAME`; `run_v4.sh` wkleja nazwę do powłoki i `python3 -c`). Nowy endpoint = test w `tests/test_server.py`.
- Pliki tymczasowe tylko w `~/video-pipeline/render/panel/`; nigdy w `~/Videos` (Syncthing), nigdy nie ruszaj `~/Videos/Nagrania`.
- Każdy podgląd wideo w UI startuje wyciszony (bez zapamiętywania odmutowania).
- Ryzyko: T3 (lokalne API uruchamia procesy, nazwa filmu trafia do powłoki w `run_v4.sh`); zmiany w `server.py` middleware / `pipeline.py` = recenzja security.
- `pg.tier_floor: T3` <!-- uruchamianie procesów + wspólne pliki projektu; stop-gate podnosi tier z diffu, nigdy nie obniża -->
- Testy nie piszą do prawdziwego `render/panel`: `MONTAZ_PANEL_DIR` na katalog tymczasowy (patrz `tests/test_server.py`).
- Kopia skryptów sprzed panelu: `tests/pipeline_baseline/*.py.orig` (strażnik zgodności, w gicie).
