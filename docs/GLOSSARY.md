# Słownik

Jedna nazwa na pojęcie. Pierwsza kolumna = UI i dokumentacja (PL), druga = kod i pliki pipeline'u.

| UI (PL) | kod / plan | Znaczenie |
|---|---|---|
| film | `film`, `plan_<film>.json` | jeden wynikowy film projektu (long, short1…); nazwa = nazwa pliku planu |
| plan | `plan_<film>.json` | wejście montażu pisane przez AI i panel: co zostaje, gdzie grafiki, najazdy, ustawienia |
| EDL | `edl_<film>.json` | wynik `build_edl.py` + `face_track.py`: lista ujęć w kolejności filmu; panel go tylko czyta |
| zostaje / wycięte | `keeps` | zakresy słów (indeksy w transkrypcji), które trafiają do filmu, w kolejności montażu |
| krawędź ujęcia | `t0` / `t1` keepa, `edges.py` | sekunda nagrania, od której / do której keep gra; wyliczana ze słów albo jawna w planie (`{"t0": …}`) |
| martwa cisza | C12, `edges.DEAD_AIR` | cisza > 0,6 s tuż przy cięciu (krawędź wpadła w długą pauzę, bo whisper rozciągnął słowo) |
| dołek energii | `edge_fix.dips`, `dip_db` | miejsce obok cięcia, gdzie 30 ms nagrania jest cichsze niż −35 dBFS: tam cięcie nie urywa dźwięku |
| ujęcie | segment (`segments[]`) | ciągły kawałek źródła między dwoma cięciami |
| zoom na cięciu | `zoom` (liczba), `zooms` | stałe powiększenie ujęcia, zmienia się na cięciach |
| drift | `zoom` {a,b,t0≈0}, `drift` | powolny zoom przez całe ujęcie |
| najazd | `emph`, punch-in | szybki zoom na słowie (`amt` = siła, `speed` = czas w s) |
| ręczny zoom | `seg_zoom` | zoom ujęcia ustawiony w panelu; klucz = wejście ujęcia w źródle (2 miejsca po przecinku) |
| bezpieczny limit | `zmax` | największy zoom, przy którym głowa zostaje w kadrze (liczy `face_track.py`); panel i pipeline nie pozwalają wyżej |
| grafika | overlay, `gfx`, `gfx_specs.json` | plansza HTML wyrenderowana do `.mov` (`gfx/render.mjs`); `kw`, `count` (licznik: liczby z tekstu odliczane od 0, GSAP), `list`, `lower`, `chap`, `cta`, `slide`, `end`, `web` |
| przesunięcie grafiki | `lead` | sekundy od słowa-zaczepu do startu grafiki |
| krytyk | `critic.py`, C1–C13 | reguły jakości (C1 brak wydarzenia wizualnego, C3 short bez zmiany obrazu, C6 ekran końcowy, C9 liczby bez źródła, C10 gramatyka montażu, C11 b-roll z planu, którego nie dało się ułożyć, C12 martwa cisza na krawędzi ujęcia, C13 cięcie w głośnej mowie…) |
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
| ramka | box (`kw`, `count`, `list`, `slide`, `web`) | grafika z boku kadru; pod nią `build_edl.py` trzyma zoom 1.0 (C2 „zoom pod ramką”) |
| render spoza panelu | foreign render | `run_v4.sh` dla tego projektu uruchomiony z terminala/zadania; panel wtedy nie zapisuje i nie przelicza |
| moment (montażu) | beat, `beats` | miejsce w tekście nagrania z intencją (hak, liczba, kontrast, puenta, lista, pytanie, wyznanie…), wykryte z treści (`beats.py`); efekt wynika z typu momentu, nie z czasu |
| gramatyka montażu | `style/grammar.json` | jedna tabela: typ momentu -> efekt (najazd, grafika, SFX, b-roll) osobno dla long i short + progi (`min_gap`, `fill_after`, `max_static`); uzasadnienia w `style/RESEARCH.md` |
| efekt momentu | `beat_fx` (EDL), `fx` | co moment dostał; kody: `punch` najazd, `spacing` za blisko silniejszego najazdu (`min_gap`), `quiet` poboczny, a wokół dość się dzieje (`fill_after`), `none` typ bez najazdu, `gfx` efektem jest grafika, `box` pod ramką grafiki, `manual` ujęcie ma ręczny zoom, `cut` słowo wycięte, `off` wyłączony ręcznie, `unknown` typu nie ma w gramatyce |
| hak: tytuł vs moment | `hook_text` vs typ `hook` | dwa różne pojęcia: `hook_text` (Ustawienia filmu) to napis tytułowy na początku, pod którym C2 nie stawia karty; moment `hook` to pierwsze zdanie z obietnicą, wykryte z tekstu, dostaje najazd |
| wymuszony | `force` | moment zawsze dostaje najazd (ustawia montażysta albo `critic.py --fix` przy C1) |
| Zwrot (etykieta) | typ `contrast` | etykieta w UI to `label` z gramatyki; kod typu się nie zmienia (`contrast` = „Zwrot”, `stat` = „Liczba”…) |
| rytm | `pace` | najazd bez momentu, tylko bo kadr stał dłużej niż `max_static` (oznaczony osobno; lepiej skrócić niż dokładać) |
| b-roll (przebitka) | `broll` (plan i EDL), `broll.py`, `stock.py` | pełnokadrowe ujęcie stock (Pexels) na słowie-zaczepie z listy tematów; głos leci dalej, napisy i grafiki są nad nim; tylko shorty (ADR-0003) |
| podpowiedź b-rollu | `effects.<format>.<typ>.broll`, `beat_fx[].broll` | INNE pojęcie niż klip: rodzaj przebitki, jaki gramatyka sugeruje dla typu momentu (`icons`, `screen`…); tylko tekst w inspektorze momentu, nic nie wstawia |
| odrzucony b-roll | `broll_rejected` | klip usunięty w panelu; ani on, ani jego słowo nie wracają przy kolejnym doborze |
| lista tematów | `broll.topics` w `style/grammar.json` | pary [regex na jednym słowie, angielskie hasło do stocku]; nowy temat = nowa para, bez kodu |
| karta pionowa | `canvas: "v"` | grafika shortu w górnym pasie kadru 9:16 (np. karta liczby z momentu „liczba”) |
| karta źródła | `type: "web"` (`url`, `find`) | grafika z przytaczaną stroną albo PDF-em: okno przeglądarki przewija się do zdania `find` i je zaznacza; tylko 16:9; projektowana ręcznie jak `slide`, nie jest momentem gramatyki (ADR-0002 pipeline'u) |
| zrzut strony | `work/gfx/web/<klucz>.png/.json` | to, co `gfx/webshot.mjs` zapisał raz z sieci; podpis karty podaje jego datę („stan na”) |
| własny zrzut | `img` (+ `box`) | INNE pojęcie niż zrzut strony: plik zrobiony ręcznie, gdy strona blokuje roboty; `box` = ramka zaznaczenia |

## Jak dodać nowy typ momentu

1. `style/grammar.json` › `beats.<typ>`: `label` (UI), `priority` (liczba; wyższy wygrywa przy `min_gap`), `tier` (`primary`/`secondary`), `intent` (zdanie dla lokalnego AI), `cues` (regexy po polsku).
2. `effects.long.<typ>` i `effects.short.<typ>`: `punch` `{amt, speed}` albo brak najazdu, opcjonalnie `gfx`, `sfx` (`pop`/`whoosh`/`ding`), `broll` (short).
3. Uzasadnienie w `style/RESEARCH.md`; test wykrycia w `tests/test_beats.py` (`DetectTest`). Brakujący klucz zgłosi `beatfx.load_grammar` z nazwą.
