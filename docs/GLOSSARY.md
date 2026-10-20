# Słownik

Jedna nazwa na pojęcie. Pierwsza kolumna = UI i dokumentacja (PL), druga = kod i pliki pipeline'u.

| UI (PL) | kod / plan | Znaczenie |
|---|---|---|
| film | `film`, `plan_<film>.json` | jeden wynikowy film projektu (long, short1…); nazwa = nazwa pliku planu |
| plan | `plan_<film>.json` | wejście montażu pisane przez AI i panel: co zostaje, gdzie grafiki, najazdy, ustawienia |
| EDL | `edl_<film>.json` | wynik `build_edl.py` + `face_track.py`: lista ujęć w kolejności filmu; panel go tylko czyta |
| zostaje / wycięte | `keeps` | zakresy słów (indeksy w transkrypcji), które trafiają do filmu, w kolejności montażu |
| ujęcie | segment (`segments[]`) | ciągły kawałek źródła między dwoma cięciami |
| zoom na cięciu | `zoom` (liczba), `zooms` | stałe powiększenie ujęcia, zmienia się na cięciach |
| drift | `zoom` {a,b,t0≈0}, `drift` | powolny zoom przez całe ujęcie |
| najazd | `emph`, punch-in | szybki zoom na słowie (`amt` = siła, `speed` = czas w s) |
| ręczny zoom | `seg_zoom` | zoom ujęcia ustawiony w panelu; klucz = wejście ujęcia w źródle (2 miejsca po przecinku) |
| bezpieczny limit | `zmax` | największy zoom, przy którym głowa zostaje w kadrze (liczy `face_track.py`); panel i pipeline nie pozwalają wyżej |
| grafika | overlay, `gfx`, `gfx_specs.json` | plansza HTML wyrenderowana do `.mov` (`gfx/render.mjs`); `kw`, `list`, `lower`, `chap`, `cta`, `slide`, `end` |
| przesunięcie grafiki | `lead` | sekundy od słowa-zaczepu do startu grafiki |
| krytyk | `critic.py`, C1–C9 | reguły jakości (C1 brak wydarzenia wizualnego, C3 short bez zmiany obrazu, C6 ekran końcowy, C9 liczby bez źródła…) |
| akceptuję | `critic_accept` | świadomy wyjątek od uwagi krytyka, z powodem |
| poprawki automatyczne | `critic.py --fix` | krytyk sam dopisuje najazdy / ogon; w panelu najpierw diff |
| zweryfikowane liczby | `verified_claims` | id grafik, których liczby mają potwierdzone źródło (C9 ich nie zgłasza) |
| podgląd | proxy, `assemble.py --proxy` | fragment ±10 s w 360p, tylko do oceny |
| pełny render | `run_v4.sh` | cały film w pełnej jakości do katalogu `out` |
| podkład | beds, `music/make_beds.py` | muzyka + efekty (SFX) dopasowane do EDL |
| projekt | katalog `work` | jeden katalog z `plan_*.json`, transkrypcją i `gfx_specs.json`; jeden panel na projekt |
| zadanie | job (`jobs.py`) | przeliczenie, sprawdzenie, podgląd, poprawki albo render w kolejce panelu (jedno naraz) |
| znacznik | playhead | pionowa linia na osi; podgląd ±10 s i nowa grafika liczą się od niego |
| zaczep (słowo-zaczep) | `word` / `find`+`near` w `gfx`, `emph` | słowo transkrypcji, do którego przyczepiona jest grafika albo najazd; po cięciach jedzie razem z nim |
| ramka | box (`kw`, `list`, `slide`) | grafika z boku kadru; pod nią `build_edl.py` trzyma zoom 1.0 (C2 „zoom pod ramką”) |
| render spoza panelu | foreign render | `run_v4.sh` dla tego projektu uruchomiony z terminala/zadania; panel wtedy nie zapisuje i nie przelicza |
