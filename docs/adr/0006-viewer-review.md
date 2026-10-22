# ADR-0006 — Recenzent-widz: agent ogląda gotowy film przed publikacją

Data: 2026-10-22 | Status: przyjęte | Drzwi: dwukierunkowe | Przegląd: po 3 filmach

**Kontekst:** Kamil (2026-10-22) zapytał, na ile nasz montaż jest „pro” i wieloagentowy. Do tej pory planem montażu zajmował się jeden agent w jednym przebiegu. Weryfikacja była regułowa: `critic.py` C1–C11, czyli tempo, liczby ze źródłem, długość napisów i ponowna transkrypcja. Nikt nie oglądał filmu oczami widza. Systemy typu reżyser + recenzent (Agentic Video Editor, Crayotter) zamykają tę pętlę osobnym recenzentem.

**Kryteria:**
1. Rozliczenie wyłącznie na subskrypcji, bez płatnego API.
2. Recenzent ma świeży kontekst i jest read-only.
3. Wynik da się sprawdzić, czyli ma czasy i dowód.
4. Mały koszt limitu: 1–3 agentów na film.
5. Uwagi trafiają do planu przez człowieka albo sesję główną, a nie automatycznie.

| Opcja | Ocena |
|---|---|
| A: `viewer_pack.py` (deterministycznie: arkusze klatek z czasem + słowa + karty) + agent wg `review/viewer.md` | spełnia 1–5; paczka z 21 min filmu powstaje w ok. 40 s |
| B: agent ogląda wideo bezpośrednio | agenci Claude Code nie czytają wideo, tylko obrazy, więc to i tak wymaga A |
| C: rozbudowa reguł `critic.py` | nie wykrywa „nudy”, sensu ani niezgodności treści i obrazu; reguły już są |
| D: fan-out wielu agentów na film (workflow) | wysoki koszt limitu tygodniowego; tylko na hasło Kamila |

**Decyzja:** A. Long dzielimy na 2 agentów po ok. 20 okien 30 s, a wszystkie shorty idą do 1 agenta. Uwagi sesja główna **weryfikuje przed naniesieniem**: każda uwaga oparta na klatkach może być artefaktem próbkowania.

- **Zmiany obrazu** (karty, timing, haki shortów) nanosi sesja.
- **Cięcia wypowiedzi** autora idą do niego jako decyzja (`DECYZJE-KAMIL.md` w projekcie).

**Pierwszy przebieg (film 2026-10-22):** 44 uwagi, 3 fałszywe.
- Dwie wynikały z artefaktów: klatki co 1,5 s „zgubiły” napisy, a narzędzie liczyło słowo zahaczone o 50 ms. Narzędzie jest poprawione: słowo musi być w ponad połowie w ujęciu, plus test.
- Trzecia: „brak powrotu do aplikacji”, choć autor mówi „możemy wrócić do tej aplikacji” w 9:09.
- **Prawdziwe i ważne:**
  - Long miał odcinki 2–4 min samej kamery (5:36–9:52, 14:52–17:14).
  - Karta pojawiała się 26 s przed zdaniem, które ilustrowała.
  - Haki 2 shortów nie pasowały do pierwszych słów.
  - Brakowało adresu strony na ekranie przy CTA.
- **Tego critic nie łapał:** C1 przepuszczał odcinki, bo liczył zmiany zoomu jako „mocne wydarzenie”.

**Konsekwencje:**
- Recenzent-widz to stały krok po renderze, a przed publikacją (README, tabela kroków).
- Po naniesieniu uwag robimy ponowny render.
- Drugi przebieg recenzenta tylko wtedy, gdy zmiany są duże.
- Otwarte: czy C1 ma przestać liczyć sam zoom jako zmianę obrazu. Do decyzji po 3 filmach, z metryką „ile uwag `boring` recenzenta pokrywa się z C1”.
