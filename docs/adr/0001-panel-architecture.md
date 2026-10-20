# ADR-0001 — Własny lokalny panel (FastAPI + vis-timeline) nad istniejącymi skryptami

Data: 2026-10-20 | Status: przyjęte | Drzwi: dwukierunkowe | Przegląd: 2026-12-01

**Kontekst:** Pipeline (`build_edl` -> `face_track` -> `critic` -> `assemble`) montuje z planu JSON. Poprawki = ręczna edycja
JSON-a i pełny render (minuty). Potrzebny panel: oś, transkrypcja, podgląd 10 s, krytyk, render; wszystko lokalnie, bez CDN.

**Kryteria:** (1) zero przepisywania logiki pipeline'u — bez nowych kluczy planu pipeline daje ten sam EDL, raport krytyka i te same komendy ffmpeg (test zgodności; wyjścia wideo nie porównujemy bajtowo); (2) utrzymuje jedna osoba + agent;
(3) podgląd zmiany < 30 s na shorcie; (4) offline.

| Kryterium (waga %) | A: ręczny JSON | B: OpenTimelineIO + NLE (Kdenlive/Resolve) | C: własny panel, vis-timeline | komentarz |
|---|---|---|---|---|
| Pipeline bez zmian (40) | 5: nic nie ruszamy | 2: eksport/import OTIO gubi najazdy, kadr z twarzy, grafiki HTML | 4: 3 małe hooki w skryptach, test zgodności komend | |
| Utrzymanie (25) | 5: brak kodu | 2: dwa formaty prawdy, adaptery | 3: ~2 tys. linii Py+JS, testy | |
| Szybkość pętli (25) | 1: pełny render | 3: szybki podgląd w NLE, ale bez naszych efektów | 5: proxy 11–15 s na short3 | zmierzone |
| Offline (10) | 5 | 5 | 5: vis-timeline 8.5.4 (npm `vis-timeline`, MIT/Apache-2.0) w `ui/vendor` | |
| **Suma** | 4,0 | 2,6 | 4,1 | |

**Decyzja:** C. Panel edytuje tylko plany i woła skrypty przez subprocess; oś to biblioteka (vis-timeline), nie własny kod rysowania.

**Rozważone alternatywy:** A — zostaje jako fallback (plan nadal można edytować ręcznie, historia panelu to wykrywa).
B — NLE nie zna naszego modelu (zoom z limitem głowy, grafiki z Playwrighta), przeniesienie = przepisanie pipeline'u.

**Konsekwencje:** zysk = pętla poprawki 6 s (przeliczenie) / ~15 s (podgląd short) zamiast pełnego renderu; pipeline zostaje
jedynym źródłem montażu. Koszt = ~2 tys. linii do utrzymania, drugi venv (fastapi, uvicorn), 3 hooki w skryptach pipeline'u,
które trzeba pilnować przy zmianach `build_edl.py`/`critic.py`/`assemble.py` (strażnik: `tests/test_pipeline_compat.py`).
Panel musi nadążać za formatem planu: nowy klucz w `build_edl.py` = nowy edytor w panelu, inaczej da się go zmienić tylko ręcznie.

**Koszt cykliczny:** 0 zł (wszystko lokalnie, bez chmury i API).

**Pułapki dla przyszłego siebie:**
- Panel ma OSOBNY venv (`panel/.venv`); `face_track.py` i `make_beds.py` idą przez `../.venv`, reszta przez systemowy `python3` — jak w `run_v4.sh`.
- `~/video-pipeline` nie jest w gicie (stary dług lintera blokuje commit); wersje sprzed panelu 3 zmienionych skryptów są w gicie panelu (`tests/pipeline_baseline/*.py.orig`, strażnik zgodności), pełny snapshot: tar `render/pipeline-snapshot-20261020-200753.tar.gz`.
- `critic.py --fix` dodaje najazdy, których `face_track` nie pokaże, gdy `zmax` ≈ zoom bazowy (short3: 5 z 6). Panel to flaguje; naprawa w pipeline = osobna decyzja.

**Powrót do założeń:** gdy pipeline dostanie własny edytor albo format OTIO z najazdami — wtedy B wraca do gry.
