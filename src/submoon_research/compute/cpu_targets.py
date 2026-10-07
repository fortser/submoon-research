"""Разбор списка CPU с явной обработкой сокращений и семейств."""
from dataclasses import dataclass
import re


def normalize_cpu(text):
    text = re.sub(r'\((?:r|tm)\)|[®™]', ' ', text or '', flags=re.I)
    return re.sub(r'\s+', ' ', text.casefold()).strip()


@dataclass(frozen=True)
class Target:
    label: str
    pattern: str
    source_line: str
    family: bool = False
    exclude_f: bool = False

    def matches(self, text):
        match = re.search(self.pattern, normalize_cpu(text))
        return bool(match and not (self.exclude_f and match.group(0).endswith('f')))

    def as_dict(self):
        return dict(label=self.label, pattern=self.pattern, source_line=self.source_line,
                    family=self.family, exclude_f=self.exclude_f)


def model_target(brand, model, line):
    label = brand + ('-' if brand.startswith('Core i') else ' ') + model.upper()
    if brand.startswith('Core i'):
        prefix = r'(?:core\s+)?'+brand.lower().split()[-1]+r'\s*[- ]\s*'
    elif brand.startswith('Xeon'):
        prefix = r'xeon\s+w\s*[- ]\s*'
    else:
        prefix = re.escape(brand.casefold()).replace(r'\ ', r'\s+')+r'\s+'
    return Target(label, r'\b'+prefix+re.escape(model.casefold())+r'\b', line)


def source_lines(text):
    for raw in text.splitlines():
        line = re.split(r'\t|\\t', raw, maxsplit=1)[0].strip()
        if not line or line.startswith(('Группа', 'Процессор', '#', 'Это')):
            continue
        if re.search(r'Core|Ryzen|Threadripper|Xeon|EPYC', line, re.I):
            yield line


def parse_targets(text):
    targets = []
    for line in source_lines(text):
        before = len(targets)
        if re.search(r'(?:Core\s+)?i[579]-', line, re.I):
            anchors = list(re.finditer(r'(?:Core\s+)?i([579])\s*-\s*', line, re.I))
            for index, anchor in enumerate(anchors):
                end = anchors[index+1].start() if index+1 < len(anchors) else len(line)
                segment = line[anchor.end():end]
                base = None
                for token in re.findall(r'\b(?:\d{4,5}[A-Za-z]{0,3}|KF|KS|K|F|S)\b', segment):
                    if token[0].isdigit():
                        base = re.match(r'\d+', token).group()
                        model = token
                    elif base:
                        model = base+token
                    else:
                        raise ValueError('Суффикс CPU без основной модели: '+line)
                    targets.append(model_target('Core i'+anchor.group(1), model, line))
        elif 'Core Ultra' in line:
            for anchor in re.finditer(r'Core\s+Ultra\s+([579])\s+(\d{3}[A-Za-z]*)', line, re.I):
                targets.append(model_target('Core Ultra '+anchor.group(1), anchor.group(2), line))
        elif re.search(r'Ryzen\s+[579]', line, re.I):
            brand = re.search(r'Ryzen\s+([579])', line, re.I)
            for token in re.findall(r'\b\d{4}[A-Za-z0-9]*\b', line[brand.end():]):
                targets.append(model_target('Ryzen '+brand.group(1), token, line))
        elif 'Threadripper' in line:
            brand = 'Threadripper PRO' if 'PRO' in line else 'Threadripper'
            for token in re.findall(r'\b\d{4}[A-Za-z]*\b', line):
                targets.append(model_target(brand, token, line))
        elif 'Xeon' in line:
            for series in re.findall(r'\b(?:W-)?(24|34)00\b', line):
                targets.append(Target('Xeon W-'+series+'00 family',
                    r'\bxeon\s+w(?:[3579]\s*[- ]?|\s*[- ]?)\s*'+series+r'\d{2}[a-z]*\b', line, family=True))
        elif 'EPYC' in line:
            for token in re.findall(r'\b(?:\d{4}[A-Za-z]*|\d{2}F\d)\b', line):
                if token not in ('4004', '4005'):
                    targets.append(model_target('EPYC', token, line))
            if '4004/4005' in line or '4004 / 4005' in line:
                targets.append(Target('EPYC 4004/4005 family', r'\bepyc\s+4\d{2}[45][a-z]*\b', line, family=True))
            if 'и т. д.' in line and 'без F' in line:
                targets.append(Target('EPYC 9004/9005 non-F family', r'\bepyc\s+9\d{2}[45][a-z]*\b',
                                      line, family=True, exclude_f=True))
        if len(targets) == before:
            raise ValueError('Не удалось разобрать строку целей CPU: '+line)
    unique = {t.label: t for t in targets}
    if not unique:
        raise ValueError('Пустой список целей CPU')
    return [unique[k] for k in sorted(unique)]


def cpu_key(text):
    text = normalize_cpu(text)
    patterns = [r'core\s+ultra\s+[579]\s+\d{3}[a-z]*',
                r'(?:core\s+)?i[579]\s*[- ]\s*\d{4,5}[a-z]*',
                r'ryzen\s+[579]\s+\d{4}[a-z0-9]*',
                r'threadripper\s+(?:pro\s+)?\d{4}[a-z]*',
                r'xeon\s+w(?:[3579]\s*[- ]?|\s*[- ]?)\s*\d{4}[a-z]*',
                r'epyc\s+(?:\d{4}[a-z]*|\d{2}f\d)']
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group().replace('-', ' ')
    return text
