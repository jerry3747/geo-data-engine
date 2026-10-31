# Project instructions

本项目的可复用 Codex Skill 位于 [`skills/geo-monitor/SKILL.md`](skills/geo-monitor/SKILL.md)。上游适配记录见 [`references/upstream.md`](references/upstream.md)。修改采集流程、指标口径或报告时，先保持 `data/`、`evidence/`、`reports/` 中的历史记录不被覆盖，再运行 `python3 -m unittest discover -s tests -v`。
