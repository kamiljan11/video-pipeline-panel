# ADR-0002 — Gramatyka montażu: efekt wynika z momentu w tekście, nie z czasu

Data: 2026-10-21 | Status: przyjęte | Drzwi: dwukierunkowe | Przegląd: 2026-12-01

**Kontekst:** Najazdy dokładał `critic.py --fix` w losowych słowach („że”, „które”), żeby żaden kadr nie stał > 40 s (long) / 7 s (short) (C1);
ten sam typ wypowiedzi dostawał różne efekty w różnych filmach. Kamil (2026-10-21): efekty mają być intencjonalne, czytane z tekstu
nagrania, i takie same dla tego samego momentu — osobno w long 16:9 i short 9:16.
W shortach Kamil prosił wprost o dobór b-rolli i grafik do momentów („bardziej można postawić na dobieranie właściwych b-rolli
i efektów”): stąd pole `broll`/`gfx` w gramatyce. Wartość dziś: montażysta widzi przy każdym momencie, jaką przebitkę lub grafikę
gramatyka przewiduje (inspektor, C10), więc dokłada je w tych samych miejscach w każdym filmie; automatycznie powstają tylko karty
liczb. Koszt: dane w `grammar.json` + jedno pole w raporcie `beat_fx`; automat wstawiania b-rolli = pytanie (3) niżej.

**Kryteria:** (1) ten sam typ momentu = ten sam efekt w każdym filmie danego formatu; (2) reguły edytowalne bez kodu;
(3) bez klucza planu pipeline działa jak wcześniej (test zgodności); (4) lokalnie, bez płatnych API.

| Kryterium (waga %) | A: krytyk dokłada najazdy co N s (stan) | B: LLM wybiera efekty dla każdego zdania | C: momenty z reguł (+opcjonalny lokalny LLM) -> tabela efektów | komentarz |
|---|---|---|---|---|
| Spójność (35) | 1: losowe słowa | 2: niedeterministyczne | 5: tabela typ -> efekt | |
| Edytowalność (25) | 2: stałe w kodzie | 3: prompt | 5: `style/grammar.json` | |
| Zgodność wstecz (25) | 5 | 3 | 5: tylko pod kluczem `beats` | test `test_pipeline_compat.py` |
| Koszt / offline (15) | 5 | 3: model 8 GB, minuty | 5: regexy, LLM tylko dla zdań bez wzorca | |
| **Suma** | 2,9 | 2,7 | 5,0 | |

**Decyzja:** C. `beats.py` rozpoznaje momenty (regexy z `grammar.json`, opcjonalnie lokalny model dla zdań bez wzorca), `beatfx.py`
wybiera efekty (priorytety, odstępy, „rytm” tylko gdy kadr stoi za długo i oznaczony jako rytm). Progi i mapowanie w `grammar.json`,
źródła w `style/RESEARCH.md` (oznaczenia SRC/HEUR/PROP).

**Konsekwencje:** zysk = efekty wytłumaczalne (pas „Momenty”, `beat_fx` w EDL), każdy najazd ma powód (typ momentu albo „rytm”),
krytyk nie dokłada losowych najazdów. Koszt = 2 nowe skrypty pipeline'u + zmiany pod kluczem w 5 skryptach (strażnik: baseline `.orig`);
regexy po polsku wymagają dopisywania wzorców; shorty z małą liczbą momentów dostają dużo „rytmu” (sygnał do cięcia, nie błąd).

**Metryka i przegląd (2026-12-01):** retencja z YouTube Studio (średni % obejrzenia, krzywa w miejscach najazdów i kart)
filmów z gramatyką vs 5 ostatnich bez niej; do tego udział „rytmu” w najazdach (`beat_fx`, cel < 30 % w longu; liczy
`python3 ~/video-pipeline/beatfx.py edl_<film>.json`; `run_v4.sh` dopisuje ten wiersz z datą po każdym udanym renderze do `render/beat_stats.log` — to są dane do przeglądu, bo EDL nadpisuje każde przeliczenie). Progi oznaczone
PROP w `style/RESEARCH.md` zmieniamy tylko z tej porównawczej obserwacji. Brak różnicy = zostaje (spójność i tak jest zyskiem),
gorsza retencja = rewizja progów `min_gap`/`max_static`.

Wersje gramatyki: EDL ma `beat_grammar.sha`; pełna treść tej wersji leży w `style/history/grammar-<sha>.json` (zapisywana raz,
przy pierwszym EDL z daną treścią), więc każdy film da się powiązać z regułami, którymi go cięto, i każdą edycję progu cofnąć.

Poza kluczem `beats` zmieniło się jedno zachowanie: `run_v4.sh` po nieudanym `build_edl.py`/`face_track.py`/`make_beds.py` nie renderuje
filmu (wcześniej renderował stary EDL i pisał DONE). `gfx/render.mjs` i `gfx/template.html` (karta `canvas: "v"`) nie mają
kopii `.orig` w testach zgodności: bez `canvas` działają jak wcześniej, kopia w snapshocie `render/pipeline-snapshot-*`.

**Koszt cykliczny:** 0 zł.

**Otwarte pytania (do Kamila):** (1) czy `~/video-pipeline` (skrypty + `style/`) ma trafić do gita — dziś historia gramatyki =
`style/history/` + snapshoty tar; (2) fail-fast w `run_v4.sh` także dla filmów bez `beats` — zostaje?; (3) b-roll w shortach:
dziś tylko podpowiedź w inspektorze i C10, bez automatu — czy budować wstawianie?; (4) czy whisper zapisuje tysiące jako „1 200”
(dwa słowa) — `beats.stat_card` obsługuje oba zapisy.
