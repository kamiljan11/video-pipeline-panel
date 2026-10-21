# ADR-0003 — B-roll z darmowego stocku (Pexels) w shortach

Data: 2026-10-21 | Status: przyjęte | Drzwi: dwukierunkowe | Przegląd: 2026-12-01

**Kontekst:** Kamil (2026-10-21, odpowiedź na pytanie 3 z ADR-0002): „b rolls you can try to find some free brools there must be
some free apis”. Cel: film gotowy do publikacji bez ręcznego szukania przebitek. Dotąd gramatyka tylko podpowiadała b-roll
(`effects.short.<typ>.broll`), wstawiał go montażysta. Badania (`style/RESEARCH.md` S12, S20, zasady 1, 9, 10): b-roll pomaga,
gdy dosłownie ilustruje to, co pada w tekście; szkodzi w momentach osobistych, przy CTA i przy haku; pokrycie ok. 30–40 %.

**Kryteria:** (1) zero kosztów i zero płatnych API; (2) licencja pozwala użyć w filmie na YouTube bez zgody autora; (3) klucz tylko
z Infisicala; (4) bez klucza planu pipeline działa jak wcześniej; (5) montażysta widzi i usuwa każdy klip.

| Kryterium (waga %) | A: Pexels API | B: Pixabay API | C: generowanie wideo (fal.ai itp.) | D: tylko podpowiedzi (stan) |
|---|---|---|---|---|
| Koszt (25) | 5: darmowe, 200 zapytań/h | 5: darmowe, 100/min | 1: płatne za sekundę | 5 |
| Licencja (25) | 5: darmowe, atrybucja niewymagana | 4: podobna, ale zapytania cache'ować 24 h, bez stałego hotlinku, prośba o podanie źródła | 3: zależy od modelu | 5 |
| Gotowość dziś (20) | 5: klucz `Pexels` już w Infisicalu | 2: brak klucza (rejestracja = Kamil) | 2 | 5 |
| Trafność (20) | 3: wyszukiwanie po angielskim haśle | 3 | 4 | 5: dobiera człowiek |
| Automatyzacja (10) | 5 | 5 | 5 | 1 |
| **Suma** | 4,6 | 3,75 | 2,7 | 4,6 (ale nie spełnia celu „gotowy film”) |

Źródła (odczyt 2026-10-21): https://www.pexels.com/license/ („Attribution is not required”; zakazy: osoby rozpoznawalne
w złym świetle lub obraźliwie, sugerowanie poparcia, sprzedaż niezmienionych kopii, znak towarowy),
https://www.pexels.com/api/documentation/ (200 zapytań/h, 20 000/mies.; wytyczne API: widoczny link do Pexels i autorów,
dlatego `broll.py` zbiera linie autorów do opisu filmu), https://pixabay.com/api/docs/ (cache 24 h, 100 zapytań/60 s, prośba o źródło).

**Decyzja:** A, z dostawcą schowanym w `stock.py` (Pixabay = jedna funkcja w `PROVIDERS` + host w `HOSTS`, gdy będzie klucz).
`broll.py` dobiera miejsca, `build_edl.py` (klucz planu `broll`) układa klipy jeszcze raz od słowa-zaczepu, `assemble.py` kładzie
je pełnym kadrem pod grafikami i napisami. Tylko shorty (`grammar.json › broll.short`; long nie ma sekcji = nic).

Reguły doboru (dane w `grammar.json › broll`, nie w kodzie):
- zaczep = słowo, które zostaje w filmie i pasuje do `topics` (regex na jednym słowie -> angielskie hasło do stocku);
- nie w pasie haka (`not_before`), nie w zdaniu z momentem typu z `avoid` (hak, wyznanie, CTA, liczba, puenta, pytanie: tam
  niesie twarz albo karta), nie pod grafiką (±0,3 s), odstęp `min_gap`, najwyżej `per_topic` razy to samo hasło,
  łącznie ≤ `max_share` filmu;
- klip: najmniejsza wersja mp4 wypełniająca kadr, dłuższa niż `dur` + 0,5 s; pierwsza sekunda pomijana, gdy klip jest dość
  długi (stockowe wejścia często są ściemnieniem); obraz bez dźwięku klipu (głos leci dalej), cięcie na twardo.

Bezpieczeństwo: plany przychodzą Syncthingiem, więc ścieżka klipu jest sprawdzana dwa razy — panel (`planops.apply_broll`:
katalog `render/broll`, nazwa `<dostawca>_<id>_<w>x<h>.mp4`) i `build_edl.py` (`broll.place`: realpath w `render/broll`, plik
istnieje; dowiązanie wychodzące poza katalog = odrzucone). Pobieranie tylko https z hosta plików dostawcy (także po
przekierowaniu), limit 150 MB, zapis atomowy. Klucz idzie z Infisicala wprost do środowiska `broll.py`
(`infisical_bridge.py run --secrets Pexels`); panel go nie widzi i nigdzie go nie zapisuje. Do Pexels wychodzi tylko angielskie
hasło z `topics` (nie transkrypcja); przekierowanie sprawdzane przed wysłaniem zapytania, niecały plik nie trafia do cache.

**Konsekwencje:** zysk = shorty dostają 1–4 przebitki bez pracy ręcznej (próba 2026-10-21 na 4 shortach projektu 2026-10-20: 2,5–10 s na film,
9–22 % długości), z listą autorów do opisu. Koszt = 2 nowe skrypty (`stock.py`, `broll.py`) + zmiany pod kluczem `broll`
w `build_edl.py`, `assemble.py`, `critic.py` (C1 liczy b-roll jako zmianę obrazu, C11 = klip, którego nie dało się ułożyć),
`head_check.py` (pomija czas b-rollu), panel (`preview.py`, pas V4, przycisk „Dobierz b-roll”).
Ryzyka: trafność wyszukiwania po słowie jest przybliżona (np. „aplikacja” -> aplikacja do jedzenia w lesie; stock z osobą
mówiącą do kamery może udawać drugiego mówcę; licencja Pexels zabrania pokazywania rozpoznawalnych osób w złym świetle,
więc hasła typu „frustrated office worker” montażysta ocenia w kontekście zdania) — dlatego klip zawsze widać na osi (V4) i usuwa się go jednym przyciskiem;
lista tematów wymaga dopisywania haseł (dane). Pexels może zmienić API albo limit: błąd dostawcy przerywa dobór z czytelnym
komunikatem, film renderuje się dalej bez nowych klipów.

**Droga cofnięcia:** usuń klucz `broll` z planu (Ctrl+Z po „Zastosuj”); bez klucza wynik identyczny jak przed b-rollem
(`tests/test_pipeline_compat.py`). Wyłączenie dla wszystkich shortów: usuń `broll.short` z `grammar.json`.

**Koszt cykliczny:** 0 zł (Pexels bez opłat; limit 200 zapytań/h, 20 000/mies.). Ryzyko kosztu: zmiana licencji albo limitów
Pexels -> drugi dostawca (Pixabay, slot w `stock.PROVIDERS`) albo powrót do podpowiedzi (droga cofnięcia).

**Metryka:** udział b-rollu w filmie i liczba klipów usuniętych ręcznie (`broll_rejected` w planie -> EDL `broll_removed`);
liczy `python3 ~/video-pipeline/beatfx.py edl_<film>.json` (`broll_share`, `broll_clips`, `broll_removed`), `run_v4.sh` dopisuje
po każdym renderze do `render/beat_stats.log`. Gdy `broll_removed` > `broll_clips` (usuniętych więcej niż zostało, czyli > połowy dobranych = zostałe + usunięte; `broll_removed` liczy całą historię planu, więc porównuj przy pierwszym doborze filmu, nie po kilku ponownych) — lista tematów albo hasła są złe. Przegląd 2026-12-01.
