# ADR-0008: panel jako publiczne repo GitHub z CI na checkoutcie pipeline'u

Data: 2026-10-24 | Status: przyjęte | Drzwi: jednokierunkowe dla historii (to, co raz pobrano, zostaje), dwukierunkowe dla CI | Przegląd: 2027-01-24

**Kontekst:** panel był lokalnym repo bez remote (kopia tylko na dysku laptopa). 2026-10-24 Kamil kazał opublikować pipeline i panel
na GitHubie („nie zgubić programu i stylu”), bez renderów i nagrań. Decyzja o publikacji obu repo, licencji i przeniesieniu mediów
na dysk Dane: ADR-0007 pipeline'u (`kamiljan11/video-pipeline`, `docs/adr/0007-public-github-repos.md`). Tu tylko to, co dotyczy panelu:
gdzie żyje, jak CI go testuje i czego publiczność wymaga od repo.

**Decyzja:**
- Osobne publiczne repo `kamiljan11/video-pipeline-panel`, klonowane do `panel/` wewnątrz checkoutu pipeline'u (układ jak lokalnie; testy importują `..`).
- Licencja „all rights reserved” (`LICENSE`), jak pipeline; vis-timeline w `ui/vendor` zostaje na swojej licencji MIT/Apache-2.0.
- CI (`.github/workflows/quality.yml`): ruff; testy jednostkowe na świeżym checkoucie `kamiljan11/video-pipeline` z tym repo jako `panel/`,
  z `MONTAZ_SLOW=0 MONTAZ_NO_SAMPLE=1` (bez projektu-próbki autora i bez e2e); gitleaks na całej historii, wbudowane reguły
  (repo nie może podać własnego `.gitleaks.toml`), obraz przypięty po digest, akcje po SHA, token checkoutu nietrwały.
- Ścieżka autora `/home/kamil-jan/video-pipeline` jest wpisana w skrypty pipeline'u: CI tworzy ją symlinkiem na runnerze.

**Odrzucone alternatywy:**
- *Jedno repo (panel jako podkatalog pipeline'u, monorepo):* jedna historia i jeden klon, ale trzeba by scalić dwie historie gita
  (`git subtree`) i zmienić lokalny układ, w którym `panel/` jest ignorowany przez pipeline. Ryzyko pomyłki przy scalaniu > zysk.
- *Submoduł gita:* wiąże wersje, ale każdy commit panelu wymaga drugiego commitu w pipeline (przesunięcie wskaźnika); przy jednej
  osobie i dwóch sesjach równolegle to stały koszt bez użytkownika.
- *CI tylko lint + gitleaks (bez testów):* tańsze i zawsze zielone, ale zielony check na repo T3 nic by nie mówił o zachowaniu (recenzja ops/product).

**Konsekwencje:** każdy commit panelu jest publikacją (historia z e-mailem autora jest publiczna). CI zależy od publicznego
`kamiljan11/video-pipeline`: gdy pipeline zmieni się niezgodnie z panelem, CI panelu zaczerwieni się bez zmiany w panelu (to zamierzone:
łapie rozjazd). Testy e2e (`test_integration.py`) i zgodność z projektem-próbką chodzą tylko lokalnie u autora. Ścieżka `/home/kamil-jan`
pozostaje długiem: obcy musi zrobić symlink albo podmienić ścieżkę.

**Koszt cykliczny:** 0 zł (GitHub Free, minuty Actions w publicznym repo bez opłat). Czas CI: szacunek 5–10 min na push (instalacja zależności), do potwierdzenia pierwszym biegiem.

**Wykonanie / rollback:** widoczność na private w ustawieniach repo; CI = usunięcie `.github/workflows/quality.yml`.

**Zmiana 2026-10-25 (Kamil):** licencja MIT zamiast „all rights reserved”, jak pipeline (ADR-0007 pipeline'u); `ui/vendor` bez zmian.
