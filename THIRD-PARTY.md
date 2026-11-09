# 第三方依赖与来源

本项目通过 Python 包依赖使用 FastAPI、Uvicorn、Pydantic、HTTPX 和 pytest，版本见 `requirements.lock.txt`。安装这些包时应保留其发行包附带的许可证及声明。对应代码和发行元数据由各项目维护。

| 依赖 | 项目 |
| --- | --- |
| FastAPI | https://github.com/fastapi/fastapi |
| Uvicorn | https://github.com/encode/uvicorn |
| Pydantic | https://github.com/pydantic/pydantic |
| HTTPX | https://github.com/encode/httpx |
| pytest | https://github.com/pytest-dev/pytest |

独立测试使用本仓库中的合成 Java 内容和临时 Git 仓库。真实源码试验使用公开 ForgeOJ 固定提交，公开对象保留原许可证和来源。发布副本不包含真实源码摘录、个人学习数据库、费用账本或真实模型响应。

网页图表使用 Mermaid 12.3.0（MIT），上游：https://github.com/mermaid-js/mermaid 。本地文件为 `static/vendor/mermaid-12.3.0.min.js`，原许可证为 `static/vendor/mermaid-LICENSE.txt`；发行包内嵌的第三方许可注释随原文件保留。`.gz` 为同一文件的确定性压缩副本。图表无需请求外部服务。

下载来源为官方 npm 发布包 https://registry.npmjs.org/mermaid/-/mermaid-12.3.0.tgz ，SHA-512 完整性为 `4113TdXeLAsMruJ79OpFL5O8+LTFXhQqZ260rF3tgUmiNAtRm4Uume04I+65PRN6yZ69J4f8rVYWW8zRk65UYw==`，提取前已核对。仅供本机验证的 Playwright/Chromium 位于忽略目录，不随运行源码提供。
