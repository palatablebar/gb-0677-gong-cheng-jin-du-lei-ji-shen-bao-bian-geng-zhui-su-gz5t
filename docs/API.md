# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 月末签证

`POST /api/visa/runs` 接收 `sample`（story=复检未结，updated=结论已更新）或 `declarations_csv`+`inspections_csv`，可选 `period`（YYYY-MM，缺省取申报唯一期间）与 `parameters.aliases`（扩展材料叫法表）；返回逐条对账结果并幂等保存，重跑同一批复用原批次。`GET /api/visa/runs` 返回历史批次；`GET /api/visa/runs/<id>` 返回详情。`POST /api/visa/runs/<id>/issue` 在可签认范围对平且无未决事项时签发正式签证，重复签发返回原证（`idempotent_replay`），结论更新后的新批次签发时本期签证升版、旧版废止。`GET /api/visa/certificates?period=` 返回签证台账；`GET /api/visa/certificates/current?period=` 返回现行签证；`GET /api/visa/runs/<id>/archive` 导出证据CSV；`POST /api/visa/upload` 接收 declarations 与 inspections 两个文件。工作台页面在 `/visa`。
