# Эталонные копии прежнего кода (W2-T008)

Дословные копии модулей до оптимизации этапа D (коммит 0a74353), используемые
только тестами эквивалентности и замером «до/после». Не импортируются научным
кодом и не развиваются. Отличие от оригинала — только импорты между копиями.

- `dense_contact_v0.py` ← `src/submoon_research/events/dense_contact.py`
- `escape_v0.py` ← `src/submoon_research/events/escape.py`
- `engine_compare_v0.py` ← `src/submoon_research/dynamics/engine_compare.py`

Загрузка: `tests/reference/loader.py::load_reference()`.
