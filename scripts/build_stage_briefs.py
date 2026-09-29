"""Build exact per-stage programme excerpts and explicit operating plans.

Run without arguments to rebuild, or --check to detect stale/missing documents.
No LLM summarization and no scientific results are produced by this script.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]


def build():
    source_path = ROOT / "Научная_программа_субспутники.md"
    source_bytes = source_path.read_bytes()
    source = source_bytes.decode("utf-8-sig").replace("\r\n", "\n")
    digest = hashlib.sha256(source_bytes).hexdigest()
    matches = list(re.finditer(r"^# (\d+)\. ", source, re.MULTILINE))
    sections = {int(match.group(1)): source[match.start():matches[i + 1].start()
                if i + 1 < len(matches) else len(source)].rstrip()
                for i, match in enumerate(matches)}
    catalog = yaml.safe_load((ROOT / "docs/stages/catalog.yaml").read_text(encoding="utf-8"))
    outputs = {}
    common = {
        "input_contract.md": ("Объекты, входные данные и обозначения", [3]),
        "mathematical_core.md": ("Математическое ядро: силы, области, меры, статистика, события", [4]),
        "scientific_rules.md": ("Контрольные эксперименты и правила научного вывода", [16]),
        "reproducibility.md": ("Форматы, воспроизводимость и проверочный выпуск", [17]),
        "resources_and_publication.md": ("Ресурсы и публикационная стратегия", [18, 19]),
    }
    provenance = ("Производная точная выдержка из [канонической программы](../../Научная_программа_субспутники.md). "
                  "Не редактировать этот файл вручную. Проверка актуальности: "
                  "`python scripts/build_stage_briefs.py --check`; обновление: та же команда без `--check`.\n\n"
                  f"SHA-256 исходного файла: `{digest}`.\n\n")
    for name, (title, numbers) in common.items():
        text = f"# {title}\n\n" + provenance
        text += "Полный текст разделов: " + ", ".join(map(str, numbers)) + ".\n\n---\n\n"
        text += "\n\n".join(sections[n] for n in numbers) + "\n"
        outputs[f"docs/context/{name}"] = text
    for stage, spec in catalog["stages"].items():
        text = f"# {stage}: рабочее досье этапа\n\n" + provenance
        text += (f"Текущий статус, исполнитель, результаты и проблемы: [SUMMARY](../../tracking/stages/{stage}/SUMMARY.md) "
                 f"и [полный индекс](../../tracking/stages/{stage}/RECORDS.md). "
                 "Это досье задаёт требования и порядок работы; наличие текста не означает завершения этапа.\n\n"
                 "## Порядок входа\n\n"
                 "1. Прочитать [общую картину](../context/project_overview.md), если её актуальная версия ещё не в контексте.\n"
                 "2. Прочитать полностью это досье и обязательные блоки ниже до научного действия.\n"
                 "3. Открыть конкретную задачу и фактические продукты предыдущих этапов; подтвердить их проверенность.\n"
                 "4. Выбрать ближайший шаг по журналу, зарегистрировать owner и критерии до расчёта.\n\n"
                 "## Дополнительное обязательное чтение для этого этапа\n\n")
        for path, reason in spec["required_reading"]:
            text += f"- [{path}](../../{path}): {reason}.\n"
        text += "\n## Продукты предыдущих этапов\n\n" + spec["inputs"] + "\n\n"
        text += "## Последовательность работ\n\n"
        for index, step in enumerate(spec["steps"], 1):
            text += f"{index}. {step}\n"
        text += "\n## Планируемый комплект выходов\n\n" + spec["outputs"] + "\n\n"
        text += ("Указанные продукты предстоит создать и проверить; их существование устанавливается по журналу и файлам. "
                 "Не переносить ожидаемые результаты в статус validated.\n\n"
                 "## Условия передачи следующему этапу\n\n" + spec["handoff"] + "\n\n"
                 "Нерешённые вопросы, альтернативы, проверки и следующие задачи записывать в журнал этапа. "
                 "При уточнении научной постановки принять решение до зависимого анализа. "
                 "Исходный текст ниже содержит научные критерии завершения и ограничения выводов.\n\n"
                 "## Полный научный раздел этапа — без сокращения\n\n---\n\n")
        text += sections[spec["programme_section"]] + "\n"
        outputs[f"docs/stages/{stage}.md"] = text
    index = {"schema_version": "1.0", "source": source_path.name, "source_sha256": digest,
             "catalog_sha256": hashlib.sha256((ROOT / "docs/stages/catalog.yaml").read_bytes()).hexdigest(),
             "outputs": {path: {"sha256_utf8_lf": hashlib.sha256(text.encode()).hexdigest(),
                                 "characters": len(text)} for path, text in outputs.items()}}
    outputs["docs/stages/source_index.json"] = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for relative, content in build().items():
        path = ROOT / relative
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        print("Stale or missing stage context:\n" + "\n".join(stale))
        return 1
    print("Stage context is current." if args.check else "Stage dossiers and shared excerpts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
