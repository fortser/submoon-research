"""W2-T008: рисунок к разбору расхождения P2-1 (по analysis.json и b_interpolation_check.json)."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
EV = ROOT/'results/evidence/W2_T008_P2-1_divergence_20261012'
INK, MUTED, GRID, SURFACE = '#1f1f1d', '#52514e', '#e1e0d9', '#fcfcfb'
SERIES = {'D-T': ('#2a78d6', '-'), 'D-B': ('#eb6834', '--'), 'T-B': ('#1baf7a', '-.')}


def main(output):
    import analyze_p2_divergence as A
    rows, scales = A.load_rows('runs/W2-P2-1-55404980'), A.case_scales()
    check = json.loads((EV/'b_interpolation_check.json').read_text(encoding='utf-8'))
    panels = [('ganymede-inner-0-0.0', 1000.0, 'а) ganymede-inner, e₀ = 0, 1000 лет'),
              ('iapetus-inner-0-0.3', 1000.0, 'б) iapetus-inner, e₀ = 0.3, 1000 лет'),
              ('himalia-outer-90-0.0', 1.0, 'в) himalia-outer, e₀ = 0, первый год')]
    plt.rcParams.update({'font.size': 10, 'axes.edgecolor': MUTED, 'axes.labelcolor': INK,
                         'xtick.color': MUTED, 'ytick.color': MUTED, 'text.color': INK})
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.4), sharey=True, facecolor=SURFACE)
    for ax, (orbit, years, title) in zip(axes, panels):
        ax.set_facecolor(SURFACE)
        for pair, (color, style) in SERIES.items():
            x, y = pair.split('-')
            pts = A.curve(rows[(orbit, years, x)], rows[(orbit, years, y)], scales[orbit])
            t = np.array([p[0] for p in pts])/A.YEAR
            d = np.maximum(np.array([p[1] for p in pts]), 1e-17)
            ax.plot(t, d, style, color=color, lw=2, label=pair.replace('-', '–'))
        if orbit in check:
            c = check[orbit]['rows']
            ax.plot([r['t_years'] for r in c], [max(r['exactB_vs_D'], 1e-17) for r in c], 'o', ms=4,
                    mfc='none', mec=INK, mew=1, label='B в концах шагов – D')
        else:
            t0 = 1.0 if years == 1000.0 else 0.01
            ref = [r for r in A.curve(rows[(orbit, years, 'D')], rows[(orbit, years, 'T')], scales[orbit])
                   if r[0]/A.YEAR >= t0][0]
            tt = np.geomspace(0.1, years, 50)
            ax.plot(tt, ref[1]*(tt/(ref[0]/A.YEAR))**2, ':', color=MUTED, lw=1.2, label='∝ t²')
        ax.axhline(1e-6, color=MUTED, lw=1, ls=(0, (6, 3)))
        if years == 1000.0:
            ax.text(900, 3e-7, 'порог τ = 1e-6', color=MUTED, fontsize=8.5, ha='right', va='top')
        else:
            ax.text(1.2e-4, 1.6e-6, 'порог τ = 1e-6', color=MUTED, fontsize=8.5)
        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.set_ylim(1e-16, 10)
        ax.set_title(title, fontsize=10.5, loc='left', color=INK)
        ax.set_xlabel('время, лет')
        ax.grid(True, which='major', color=GRID, lw=0.6)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        ax.legend(frameon=False, fontsize=8.5, loc='lower right' if years == 1000.0 else 'upper left')
    axes[0].set_ylabel('расхождение d = max(|Δr|/a, |Δv|/na)')
    fig.tight_layout()
    fig.savefig(output, dpi=150, facecolor=SURFACE)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT/'reports/analysis/W2_P2-1_divergence_20261012.png'))
