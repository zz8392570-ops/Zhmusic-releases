# ZhMusic 安装包与更新源

[下载最新 IPA](https://github.com/zz8392570-ops/Zhmusic-releases/releases/latest/download/ZhMusic-unsigned.ipa)

SideStore 源：

```text
https://github.com/zz8392570-ops/Zhmusic-releases/releases/download/sidestore/source.json
```

首次构建成功后，以上下载地址才会可用。安装仍需自己的有效签名。

## 手机发起打包

打开本仓库 **Actions → Build ZhMusic → Run workflow**，选择源码仓库后运行。
此工作流使用 Ubuntu 调度临时公开仓库中的 macOS 构建，下载并校验 IPA 后删除临时仓库，
最后把安装包和更新源发布到本仓库。只上传当前已提交版本，不上传 Git 历史。
排队和构建期间源码快照仍可被复制；删除仓库不能收回其他人已取得的副本。

本仓库只保存调度工作流、说明和安装包，不保存应用源码。
源码仓库的原自动流程在首次成功迁移后停用；正式源码仓库原手动 IPA 工作流保留。
质量和原生检查仍在每次临时构建内执行。

## 密钥

仓库 **Settings → Secrets and variables → Actions** 中设置 `ZHMUSIC_BUILD_TOKEN`，
使用属于 `zz8392570-ops`、拥有 `repo`、`workflow`、`delete_repo` 权限的 classic token。
密钥仅供云端调度使用，不传入临时仓库的 macOS 作业。
Token 到期后更新同名 Secret。标准运行器可用性和计费以 GitHub 当时的规则为准。

## 构建失败

工作流会尝试删除本次临时仓库；已下载的 IPA 和会话记录另保留为 Actions artifact 7 天。
网络故障、权限撤回或作业被强制结束可能阻止删除，需在恢复后按会话记录重试清理。
安装包发布前使用草稿 Release，上传失败时保留已有最新版本。
Zhmusic-releases
