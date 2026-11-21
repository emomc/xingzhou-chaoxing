# 星舟网站

此目录是独立的 Go 网站服务端源码，与桌面/移动客户端分开维护。

构建、初始化、运行和验证见[仓库首页](../README.md)。仅构建 Linux 服务端程序：

```bash
bash ../scripts/build-linux.sh
```

网站使用独立的本地状态目录，通过后端令牌文件连接 Python API。可在管理员连接设置中配置官方 OpenAI 兼容供应商；令牌和供应商密钥不会通过配置查询接口返回原文。

`internal/server/static/mobile.css` 提供手机和平板布局。`scripts/*.test.mjs` 是基于本地模拟 DOM 的离线回归测试；移动布局脚本使用假后端，不需要真实学习账号。
