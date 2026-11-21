# 星舟 · Xingzhou

**把超星课程、任务和学习资料放进一个可自行部署的工作台。**

星舟是从超星项目中独立整理的服务端版本：Go 网站控制台配合 Python 后端，提供账号管理、课程与章节查询、任务状态、AI 接口配置和题库管理。

本仓库只发布超星相关源码，不包含智慧树、桌面/手机客户端、网页 AI 浏览器服务，以及任何运行账号或数据。它是独立维护的衍生项目，与超星官方没有隶属关系。

## 功能

- 网站登录、管理员和普通用户、账号分配与访问控制。
- 课程、章节及任务点查询，任务创建、暂停、恢复和归档。
- 实时状态、任务事件、视频进度看护与节奏展示。
- OpenAI 兼容 API 接入，文本和图片可分别选择模型。
- 题库、图片答案核对、笔记、存储与资源状态。
- 适配手机和平板的网页界面；默认只监听本机回环地址。

## 快速开始（Linux）

需要 Python **3.13+**、Go **1.26+**，以及可用的 C/C++ 运行库（`ddddocr` / ONNX 依赖）。Node.js 只在运行前端离线测试时需要。

```bash
git clone https://github.com/emomc/xingzhou-chaoxing.git
cd xingzhou-chaoxing
python3.13 -m venv .venv
.venv/bin/python -m pip install -r chaoxing-3.1.4/requirements-service.txt
python3.13 scripts/init-config.py
bash scripts/build-linux.sh
```

初始化只生成私有本地配置，不登录课程平台、不创建学习任务。管理员用户名和初始密码保存在 `runtime/web.env`；后端令牌保存在 `runtime/backend/api-token`。这些文件不会被 Git 跟踪，初始化也不会覆盖已存在的配置。

分别在两个终端启动：

```bash
# 终端 1：本地 API
bash scripts/run-backend.sh

# 终端 2：网站
bash scripts/run-web.sh
```

浏览器打开 **http://127.0.0.1:3313/**，使用 `runtime/web.env` 中的账号登录。后端健康检查为 `http://127.0.0.1:3100/healthz`。

如在另一台 Linux 服务器运行，可使用 SSH 本地转发访问网页；不要直接把私有 API 暴露到公网：

```bash
ssh -L 3313:127.0.0.1:3313 your-user@your-server
```

## AI 接入

在管理员的“连接设置 → API 上游配置”中填写自己的供应商地址、模型和 API Key；需要时单独配置图片模型，再切换答题通道为 API。密钥通过后端加密存储，查询配置时不会返回密钥原文。

项目不赠送 API Key，也不内置任何人的订阅或免费额度。费用、免费模型和速率限制以供应商当时的规则为准。网页 AI 桥接属于可选外部服务，不包含在本次开源范围内；直接使用官方 API 无需启动浏览器 AI 服务。

## 数据与任务

- `runtime/backend/`：账号、任务、题库及其加密密钥。
- `runtime/web/`：网站登录、设置及本地存储。
- `runtime/web.env`：初始管理员配置。
- `bin/`：自行构建的 Linux 网站程序。

任务需要用户明确创建。已有状态目录中的排队、运行或重试任务可能在后端重新启动时恢复；如需停留在管理模式，应先在网站暂停任务。备份数据库时也要保留相应的加密密钥，恢复时保持成套。

## 开发与验证

测试使用模拟账号、模拟课程和本地假接口，不代表真实平台的可用性或吞吐保证。

```bash
python3.13 scripts/check-release.py
(cd chaoxing-3.1.4 && ../.venv/bin/python -m unittest discover -s tests)
(cd server-web && go test ./...)
node --test server-web/scripts/*.test.mjs
```

仅构建 Linux 服务端程序：

```bash
bash scripts/build-linux.sh
```

## 项目结构

```text
server-web/          Go 网站、静态资源与测试
chaoxing-3.1.4/      Python 后端、超星接口与测试
scripts/            初始化、Linux 构建、启动与发布检查
docs/               来源、发布范围和可选服务说明
runtime/            启动后生成，禁止提交
```

`chaoxing-3.1.4` 是上游来源目录名，不是星舟的发行版本号。首个独立源码版本为 **0.1.0**。

## 来源与许可证

后端基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 的 v3.1.4 工作副本演进；原有版权说明和 GPL-3.0 许可已保留。本项目以 **GPL-3.0** 发布，第三方静态资源保留其单独的许可说明，详见 [LICENSE](LICENSE)、[NOTICE](NOTICE) 和 [来源说明](docs/UPSTREAM.md)。

请仅操作自己有权管理的账号和课程，并自行确认所在学校、课程及平台的使用规则。贡献前请阅读 [贡献说明](CONTRIBUTING.md)，敏感信息请勿提交到公开 Issue。
