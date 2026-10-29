# ZhMusic 安装包与更新源

[下载最新 IPA](https://github.com/zz8392570-ops/Zhmusic-releases/releases/latest/download/ZhMusic-unsigned.ipa)

SideStore 源：

```text
https://github.com/zz8392570-ops/Zhmusic-releases/releases/download/sidestore/source.json
```

安装仍需自己的有效签名。

## 手机发起打包

打开本仓库 **Actions → Build ZhMusic → Run workflow**，直接运行即可，固定使用私有源码仓库 `Zhmusic`。
此工作流使用 Ubuntu 调度临时公开仓库中的 macOS 构建，下载并校验 IPA 后删除临时仓库，
最后把安装包和更新源发布到本仓库。只上传当前已提交版本，不上传 Git 历史。
排队和构建期间源码快照仍可被复制；删除仓库不能收回其他人已取得的副本。

本仓库只保存调度工作流、说明和安装包，不保存应用源码。
`Zhmusic-test` 已停止维护，不再同步更新，也不再作为打包来源。
源码仓库的原自动流程已停用；正式源码仓库原手动 IPA 工作流保留。
质量和原生检查仍在每次临时构建内执行。

## 密钥

仓库 **Settings → Secrets and variables → Actions** 中设置 `ZHMUSIC_BUILD_TOKEN`，
使用属于 `zz8392570-ops`、拥有 `repo`、`workflow`、`delete_repo` 权限的 classic token。
密钥仅供云端调度使用，不传入临时仓库的 macOS 作业。
Token 到期后更新同名 Secret。标准运行器可用性和计费以 GitHub 当时的规则为准。

## Cloudflare Pages 自动同步

在本仓库 Actions Secrets 中另添加：

- `CLOUDFLARE_API_TOKEN`：限定当前账户的「账户 → Cloudflare Pages → 编辑」令牌。
- `CLOUDFLARE_ACCOUNT_ID`：Cloudflare 账户的 32 位 ID。

每次 **Build ZhMusic** 成功发布后，自动调用 **Sync Cloudflare Pages**，
首次自动创建 `zhmusic-download` Pages 项目（生产分支 `main`）。实际域名以运行摘要为准。
也可单独手动运行 **Sync Cloudflare Pages**，同步已发布的最新版本，不重新打包。
同步失败不会删除已发布的 GitHub IPA，可修复密钥后单独重试。

只向 Pages 上传公开 Release 的源 JSON、图标和 IPA，不上传应用源码或凭据。
先校验 GitHub 资产 SHA-256、ZIP 完整性和包内版本，上传后再校验公网下载内容。
源的标识、版本和权限保持一致，图标和安装包地址改为 Pages 地址。
单次部署只包含更新源当前版本；旧版本仍保留在 GitHub Releases。
Pages 单文件上限 25 MiB，超限会停止同步，GitHub 下载仍可用。
源文件禁用缓存，IPA 使用版本路径。免费计划和域名可达性以 Cloudflare 实际政策及网络为准。
首次启用前，应在手机开启 LocalDevVPN 后测试运行摘要给出的源地址和安装包下载。

## 构建失败

工作流会尝试删除本次临时仓库；已下载的 IPA 和会话记录另保留为 Actions artifact 7 天。
网络故障、权限撤回或作业被强制结束可能阻止删除，需在恢复后按会话记录重试清理。
安装包发布前使用草稿 Release，上传失败时保留已有最新版本。
失败日志和会话记录会保存为私有源码仓库 `Zhmusic` 的草稿 Release；保存失败时，
调度运行会显示失败提示。公开恢复 artifact 不包含失败日志。

独立的 **Cleanup expired temporary builds** 工作流每半小时尝试清理超过三小时的
临时仓库。清理前核验账户、专用名称和创建标识、创建时间、仓库 ID，并跳过任何存在
未结束或状态不明构建的仓库；也可在 Actions 手动触发。定时任务可能延迟，Token 到期、
权限失效或 GitHub 不可用时无法保证自动清理，检查会报告失败。
