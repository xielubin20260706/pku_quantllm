# 原始源码仓库

本目录保存课程学习用的源码快照。三个仓库均从 Gitee 克隆，使用 `--depth 1` 浅克隆；源码未经本 Wiki 修改。下面的提交信息用于让源码解读可复现。后续 `git pull` 更新仓库时，请同步检查对应 Wiki 页面。

| 模块 | Gitee 仓库 | 版本 | 克隆时 HEAD |
|---|---|---|---|
| vnpy 核心框架 | [vnpy/vnpy](https://gitee.com/vnpy/vnpy) | 4.4.0 | `2ae433660477f9f6a10b0683603879e59072659f` |
| CTP Gateway | [vnpy/vnpy_ctp](https://gitee.com/vnpy/vnpy_ctp) | 6.7.11.4 | `1274a048027ba204d0dde03953384ee7e2513979` |
| CTA Strategy | [vnpy/vnpy_ctastrategy](https://gitee.com/vnpy/vnpy_ctastrategy) | 1.4.1 | `5da04d953107c1d54385ffd2aeafd43d8fba0a16` |

以上提交日期均为 2026-10-04。仓库分别位于 [`vnpy/`](vnpy/)、[`vnpy_ctp/`](vnpy_ctp/) 和 [`vnpy_ctastrategy/`](vnpy_ctastrategy/)。

## 学习提示

- `vnpy_ctp` 依赖声明只要求 `vnpy>=3.0.0`，`vnpy_ctastrategy` 则要求 `vnpy>=4.4.0`；这些是各自的包元数据下限，不等于完整的兼容性测试矩阵。当前三个 HEAD 来自同一天，但实际安装时应优先采用课程环境或官方兼容版本。
- `vnpy_ctp` 含有 CTP 原生库及构建文件；理解 Python 侧适配层时重点读 `vnpy_ctp/gateway/ctp_gateway.py`，底层 API 绑定与平台构建另行研究。
- 请不要把 SimNow 或实盘账号信息写入源码目录或提交到公开仓库。
