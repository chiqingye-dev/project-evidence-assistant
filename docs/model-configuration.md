# 模型配置

默认 `run.py` 不读取 Key，也不调用模型。0.5 普通正文默认使用 `SimpleSectionAdapter`；目录、章节提纲和追问仍沿用旧适配器，其校验错误暂不提供两次纠正。`--legacy-model-protocol` 可显式恢复旧协议，用于历史兼容。

## 一次配置环境变量

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure-model.ps1
```

在本机终端隐藏输入一次 Key。脚本将其保存为当前 Windows 用户的 `DEEPSEEK_API_KEY`，不发送模型请求，不写入命令行、聊天或项目文件。用户环境变量是本机持久配置，其他以该用户身份运行的程序可以读取；需要移除时在 Windows 环境变量设置中删除该项。

程序优先读取进程环境变量；Windows 下再读取当前用户的同名变量，因此不需要重启 Codex。其他系统在启动服务前设置环境变量。不要把 Key 发到聊天或提交 Git。

## 启动模型功能

沿用既有 `data/` 和本机价格配置：

```powershell
& .\.venv\Scripts\python.exe -B run.py --enable-paid-model `
  --enable-query-model --enable-submission-model `
  --enable-consumption-model --enable-judging-model --enable-followup-model
```

需要本机 HTTP 代理时附加 `--deepseek-proxy-port 7890`，只影响本进程，不改系统网络。`Start-WithDeepSeek.ps1` 是历史实验入口，新开发会话直接读取环境变量。

首次启用需要本机确认预算与官方价格；升级沿用已有授权和累计账本。价格来源是 DeepSeek 官方价格页，`prices/deepseek-verified.json` 仅留本机。现有费用代码要求真实、匹配、未过期的价格核验；每次请求不会新建入口、封印、manifest 或核验器。不要手改核验时间冒充新的官方价格核对。

本项目既有累计预算为 50 元，单任务 3 元、单次预留上限 2.5 元。按保守高峰单价核算，不等于平台实际账单或余额。纠正输出也收费，不能重置任务预算。丢失可信 usage 时保留预留并停止，先查账再决定下一步。

## 输出与诊断

正文模型只提交：

```json
{
  "blocks": [
    {"heading": "入口", "text": "依据已读源码说明行为。", "evidence_ids": ["本轮读取返回的ID"]}
  ],
  "gaps": ["未读取被调实现，不能确认其内部行为。"]
}
```

宿主填写标题、章节 ID、候选支持状态，推导 `block:1:text` 等位置。已有 ID 只能证明引用的是本轮读过的片段，不能证明这段话正确。

每次供应商响应先写入忽略提交的 `data/model-responses/任务ID/`，再解析。保存响应正文及 HTTP 状态，不保存请求授权头；脱敏当前 Key、其 JSON 转义形式及 Bearer 值。超过 256 KiB 的响应只保留有标记的前缀。请将该目录当作本机诊断数据，不要整目录公开。

正文 Schema、字段、引用 ID 错误可以反馈给模型，最多两次。错误文本也先脱敏。权限拒绝、读取越界、来源变化、账单不确定不属于这种纠正；所有失败记录和费用保留。

## 固定评测

`evaluation/fixed_v05.json` 默认 10 题 × 3 次 × 2 方法，共 60 个任务。运行前核对一次公开来源，随后复用有效会话；每个任务仍检查固定对象和费用边界。

```powershell
& .\.venv\Scripts\python.exe -B scripts/evaluate.py `
  --data-dir .\data --source D:\path\to\ForgeOJ --proxy-port 7890
```

本机保存逐次结果、原始响应和费用，公共报告只汇总。中断后用 `--resume data/evaluations/已有批次` 继续尚未执行的任务，已完成失败不会自动重跑。自动要点检查只是词组覆盖；人工开发者复核也必须注明身份，不能称为独立盲评。
