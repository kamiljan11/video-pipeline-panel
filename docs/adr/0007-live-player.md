# ADR-0007: odtwarzacz na żywo, czyli film składany w przeglądarce z EDL

Data: 2026-10-24 | Status: przyjęte | Drzwi: dwukierunkowe (wyłączenie = usunięcie bloku `#live`; pipeline nietknięty) | Przegląd: po 3 projektach

**Kontekst:** do tej pory jedynym sposobem obejrzenia montażu był „Podgląd ±10 s”, czyli prawdziwy render 360p (`assemble.py --proxy`). Trwa kilkadziesiąt sekund i pokazuje tylko 20 s. Kamil chce przy montażu oglądać film od dowolnego miejsca w czasie rzeczywistym, w dużym oknie, a edycje planu mają być widoczne bez renderu. `assemble.py` pozostaje jedynym źródłem prawdy o obrazie.

**Kryteria:**
1. Start odtwarzania z dowolnego miejsca w czasie poniżej 1 s.
2. Edycja widoczna w kilka sekund, bez przeładowania strony.
3. Wierność wobec `assemble.py` dla cięć, kadru, zoomu, kart, PiP i napisów.
4. Brak zmian w skryptach pipeline'u.
5. Kopie robocze tylko w `render/panel`.

| Opcja | Ocena |
|---|---|
| A: przeglądarka składa film z EDL: dwa `<video>` z lekkiej kopii nagrania, kadr i zoom transformacją CSS wg wzorów `assemble.vf_for`, karty jako VP9 z alfą, napisy z `.ass` | spełnia 1–5. Przybliżenia wymienione niżej |
| B: render ciągły (`assemble.py --proxy` całego filmu po każdej edycji) | nie spełnia 1–2: long 18 min to kilka minut renderu na każdą edycję |
| C: render segmentów w tle + HLS | spełnia 3, ale nie 2 (zmiana zoomu = ponowny render ujęć). Trzeba by też utrzymywać drugi tor renderu obok `assemble.py` |
| D: WebCodecs / ffmpeg.wasm w przeglądarce | najwięcej kodu. Klatkowa dokładność nie jest tu potrzebna, a koszt CPU laptopa jest wysoki |

**Decyzja:** A.

Podział kodu:
- **Matematyka** jest w `ui/live_core.js` (czysta, bez DOM): mapowanie czasu filmu na ujęcie, zoom w chwili t, prostokąt kadru (static / perspective / pas 9:16 / track) oraz widoczność kart i PiP.
- **Test** `tests/test_live.py` wyjmuje z `assemble.py` jego własne `vf_for`/`keys_expr`/`track_expr` i liczy wyrażenia ffmpeg, które budują. Zmiana kadrowania w `assemble.py` wywala ten test, dopóki odtwarzacz za nią nie pójdzie.
- **Model filmu** w czasie wyjścia (`montazownia/live.py`) jest czysty i ma też parser `.ass`.
- **Pliki** obsługuje `montazownia/livemedia.py`.

Kopie robocze (`render/panel/cache/<projekt>/`), robione jednym krokiem zadania (`mediaconv.py`, podproces):
- **kopia nagrania:** 720p H.264, klatka kluczowa co 0,5 s, AAC po `loudnorm`;
- **karty:** `gfx/<id>.mov` (ProRes 4444) zamienione na VP9 `yuva420p` w WebM;
- **podkład:** `make_beds.py` na kopii EDL, w `beds/<projekt>/<film>.live/`.

Kopię rozpoznaje znacznik `realpath|rozmiar|mtime`. Nowe nagranie albo przerenderowana karta są konwertowane od nowa, a niezmienione nigdy. Pliki powstają jako `.part` i dostają docelową nazwę dopiero po zakończeniu. Starsze wersje tego samego wejścia `cleanup` usuwa po udanym przebiegu.

**Odstępstwo od „jedno zadanie naraz” (ADR-0001):** przygotowanie odtwarzacza idzie osobnym torem (`Panel.media`, drugi `Runner` ze wspólną numeracją zadań). Kopia nagrania longa to ok. 1,5 min. W jednej kolejce blokowałaby przeliczenie po edycji, a to edycja ma być widoczna od razu. Tor jest bezpieczny, bo pisze wyłącznie do `render/panel`, nigdy do `work`. Karty czekają, gdy działa render grafik albo pełny render (`gfx_busy`), żeby nie konwertować w połowie zapisanego `.mov`. Stop zatrzymuje oba tory. Limit to 1 h na przygotowanie.

**Co startuje samo:** karty i podkład po każdej nowej wersji EDL (raz na wersję, więc karta, która zawsze pada, nie zapętla się). Jednorazowa kopia nagrania czeka na klik „Przygotuj odtwarzacz” albo na Play.

**Przybliżenia wobec pełnego renderu:**
- **Cięcia:** ±1 klatka. Drugi `<video>` startuje 0,4 s przed cięciem i dogania czasem odtwarzania. Zegar może przeskoczyć o ok. 50 ms na cięciu.
- **Napisy:** tekst DOM z fontem przeglądarki zamiast libass (skala `FONT = 0.7`, dobrana na oko do klatek z renderu). Łamanie wierszy może się różnić.
- **Grading:** filtr CSS przybliża domyślny `eq`. `unsharp` jest pominięty, a własny `grade` z EDL nie jest naśladowany.
- **Dźwięk:** głos z `loudnorm` kopii zamiast łańcucha `audio` z `assemble.py`. Muzyka bez ściszania pod głosem (ducking).
- **PiP:** wycinek z ujęcia pod spodem (`assemble.py` wycina ze złożonej klatki, więc b-roll pod PiP nie pokaże się w PiP).

Gdy liczy się dokładność (miks, libass, grading), zostaje „Podgląd ±10 s”.

**Konsekwencje:**
- Prawa kolumna dostała zakładki (Inspektor, Krytyk, Podgląd ±10 s, Ustawienia, Log), a lewa oddała miejsce odtwarzaczowi.
- „Duży podgląd” (T) chowa prawą kolumnę i zwija oś do pasów V1–V4 + krytyk.
- Przy zamknięciu panelu uvicorn czeka maksymalnie 5 s na otwarte strumienie wideo (`timeout_graceful_shutdown`).
- Cache kopii (~100 MB na nagranie) czyści się razem z innymi cache martwych projektów (`CACHE_KINDS`).
- Otwarte do przeglądu: czy kopia nagrania ma startować sama przy pierwszym otwarciu projektu. Metryka: ile razy na projekt trzeba było kliknąć „Przygotuj”.
