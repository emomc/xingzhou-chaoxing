# 来源与公开范围

## 来源

Python 核心来源于 Samueli924/chaoxing 的 v3.1.4 源码工作副本：

- 上游：https://github.com/Samueli924/chaoxing
- 对应许可：https://github.com/Samueli924/chaoxing/blob/v3.1.4/LICENSE

本工作副本在此基础上增加了可持久化的服务 API、任务管理、共享题库、AI 供应商配置、图片处理和独立 Go 网站。目录名保留来源标记；`pyproject.toml` 中的历史核心版本不作为星舟发行版本。

公开版保留源码中的上游署名、根目录及后端 LICENSE，以及 Lucide 的 ISC 许可。没有用新许可证替换原有第三方许可。

## 本次包含

- 当前 `server-web` 网站源码、静态资源、Go 与前端离线测试。
- 超星 `api`、`backend`、`main.py`、必要资源与 Python 测试。
- 不含凭据的配置模板、独立 Linux 初始化和启动脚本。

## 本次不包含

- 智慧树、桌面与手机客户端。
- 网页 AI 浏览器服务的源码、用户浏览器资料或登录状态。
- 任何历史账号、API Key、Cookie、日志、数据库、截图、部署记录或二进制文件。
- 原工作目录的 Git 历史；仓库从已检查的源码快照建立。

`chaoxing-3.1.4/deploy/linux` 中保留了可选网页 AI 适配器的初始化/兼容性测试文件。它们引用单独部署的外部 WebAI2API 服务，该服务不在本仓库内。主项目的部署入口是根目录 `scripts/`，使用官方 OpenAI 兼容 API 不依赖这套可选文件。
