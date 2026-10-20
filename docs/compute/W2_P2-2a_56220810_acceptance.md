# Приёмка run W2-P2-2a-56220810 (решение W2-D036)

Статус: **принят** 2026-10-16, claude-opus-20261014. Решение пользователя 2026-10-16 20:34 MSK:
«Делать 1а и 2».

## Почему не прошла приёмка в WSL (W2-I035)

`physical_inputs` считал большую полуось хозяина через `np.linalg.norm` и `np.dot`, а
`elements_to_cartesian` поворачивал старт через `@`. Это операции OpenBLAS, который выбирает ядро
по процессору: SkylakeX (AVX-512 — сервер TR PRO 9975WX, контейнер Claude) и Haswell (AVX2 —
i5-9400 пользователя) суммируют в разном порядке. Радиус Хилла Япета отличался на 2 ULP, отсюда
другие старты всех 384 единиц Япета и 3168 отпечатков (это весь объём Япета, а не мера
`canonical_volume`, как считалось в W2-C060). Физически разница ~1e-16 отн.

Доказательство (W2-C062): принудительное `OPENBLAS_CORETYPE=Haswell` воспроизводит units_sha256 WSL
`8bb459e8…`, `SkylakeX` — серверный `c7979471…`.

## Порядок приёмки этого run (W2-D036)

1. Код — **архивный пакет run**: `scratch/p2_2a_56220810/bundle.tar.gz`,
   SHA-256 `a81aba2800ee53a66d0c48892a2317b873ad281cbd1ac70dbb0829774b34711a`, 439 файлов манифеста
   сверены. Заменён только `scripts/verify_p2_2_run.py` (версия с `--float-rtol`,
   SHA-256 `eb15c26e469700b217d67048db65781de03fcc7a40f25eea4c40bc34477b7f42`); генератор, стенд и
   входы — как на сервере.
2. Данные — архив выгрузки `scratch/p2_2a_56220810/results_56220810.tar.gz`,
   SHA-256 `7ab1989efa57b9174a3c3f392dc96805a527965bb8c83a8cb19160577226cb87`.
3. Окружение — процессор с AVX-512 и `OPENBLAS_CORETYPE=SkylakeX` (то же ядро, что на сервере).
   Выполнено в контейнере Claude: Python 3.13.16, glibc 2.39, numpy 2.5.3, scipy 1.18.1.
4. Команда:
   `OPENBLAS_CORETYPE=SkylakeX python scripts/verify_p2_2_run.py --run runs/W2-P2-2a-56220810 --expect-full --expect-decision-ready --float-rtol 1e-12`
5. Результат: **ПРИНЯТ** — 9504/9504 заданий, все отпечатки, units_sha256, счётчики summary и
   decision совпали; допуск 1e-12 понадобился только для 31 производной float-величины (стоимость,
   cp_upper отдельных ячеек; max отн. 1.4e-15 — суммы и квантили в Python 3.13/scipy 1.18 против
   3.10/1.15). Без допуска — провал только по этим 31 величинам; при `Haswell` — провал, тождественный
   W2-C060 (отрицательный контроль).

Доказательства: `results/evidence/W2_I035_diag_20261016/acceptance_D036.json`,
`acceptance_container_SkylakeX.json`, `acceptance_container_Haswell.json`, `analysis_float_diff.txt`,
`openblas_coretype_probe.txt`.

Что допуск НЕ ослабляет: состав заданий, отпечатки, seeds, units_sha256, целые счётчики, строки,
вердикты и структура сравниваются точно; допуск ограничен ≤ 1e-9 и по умолчанию равен 0.

## Генератор после W2-I035

С 2026-10-16 малые векторы и матрицы считаются без BLAS (`src/submoon_research/exact_linalg.py`,
`math.fsum`, режим `exact-fsum-v1`), и старты одинаковы при любом ядре OpenBLAS (тест
`tests/unit/test_exact_linalg.py`; на i5 units_sha256 полного P2-2a `65a6ad12…` при Haswell и
Sandybridge — как в контейнере). Новые run пишут `generator_numerics` в summary.json
(`admission_p2_2.py`, `admission_p2_horizons.py`).

Run, созданные до исправления (в summary.json нет `generator_numerics`: W2-P2-2a-56220810, run P2-1),
приёмщики `verify_p2_2_run.py` и `verify_p2_run.py` сами проверяют в режиме `legacy-blas`, который
повторяет прежние операции numpy/BLAS бит в бит. Совпадение стартов возможно только на процессоре с
тем же ядром OpenBLAS, что при расчёте: P2-2a 56220810 — AVX-512 (`OPENBLAS_CORETYPE=SkylakeX`;
текущий код в контейнере Claude: ПРИНЯТ 9504/9504 с `--float-rtol 1e-12`); run P2-1 на i9 (AVX2) —
на i5 пользователя. При другом ядре приёмщик отказывает с объяснением. Продолжение или
`--evaluate-only` старого run текущим стендом невозможны (отпечатки стартов другие) — только новый run.
