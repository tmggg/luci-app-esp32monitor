# luci-app-esp32monitor

面向 ESP32 的只读 OpenWrt 状态接口及 LuCI 配置页面。纯 Shell/JavaScript，架构为 all，无额外 kmod 依赖；依赖 OpenWrt 的 procfs、sysfs、UCI 和 uHTTPd CGI 服务。

## 使用

编译：先在 menuconfig 的 LuCI → Applications 中选中 luci-app-esp32monitor，再在 OpenWrt 根目录执行 `make package/luci-app-esp32monitor/compile V=s`。简体中文界面需同时安装 luci-i18n-esp32monitor-zh-cn。
安装后在 **服务 → ESP32 监控（ESP32 Monitor）** 中配置并保存应用。
接口默认关闭，安装初始化会生成 32 位 UUID 随机令牌（移除连字符）。配置每次请求读取，无需独立守护进程。

```sh
curl -H 'X-API-Token: YOUR_TOKEN' http://ROUTER/cgi-bin/esp32-status
# 启用 URL Token 时也可以使用：
curl 'http://ROUTER/cgi-bin/esp32-status?token=YOUR_TOKEN'
```

## 配置

配置文件 `/etc/config/esp32monitor`，命名节 `main`，类型 `esp32monitor`：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| enabled | 0 | 启用接口；关闭返回 HTTP 503 |
| api_token | 安装时随机生成 | LuCI 要求 8–128 位字母、数字、下划线或连字符 |
| wan_device | 空 | 自动通过 WAN ubus 状态或默认路由检测，或手动指定设备 |
| interfaces | 空列表 | 监控全部非 lo 设备，或用 UCI list 指定设备 |
| only_link_up | 1 | 仅返回 carrier=1 或 operstate=up 的接口 |
| temperature_source | auto | 自动探测 thermal/hwmon，或指定完整传感器路径 |
| s3_gif_enabled | 0 | 启用 S3 GIF 屏保支持；勾选后显示屏保类型选项 |
| screensaver_type | clock | 屏保类型二选一：clock（时间屏保）、gif（GIF 屏保） |
| screensaver_timeout | 60 | 屏保等待时间，单位秒，整数 0–86400；0 禁用 |
| allow_query_token | 1 | 允许 URL 中的 token 参数；非空请求头优先 |

采样等待固定 1 秒，不提供 sample_interval。指定 WAN 不改变接口筛选。
温度路径仅允许枚举到的 thermal/hwmon 属性，读不到或格式无效返回 null。
URL Token 只使用上述 URL 安全字符，不做百分号解码。禁用 URL Token 后客户端必须发送 X-API-Token；部分 uHTTPd 构建不能向 CGI 转交此请求头。
原脚本的 `/etc/esp32-status.token`、固定默认令牌和环境变量不再作为配置来源；已有客户端需要改为新令牌。

## 响应与兼容

保留原 JSON 字段：ok、hostname、cpu_percent、memory_percent、temperature_c、uptime_seconds、wan_device、interface_count、interfaces。
新增系统日期时间字段（字符串）：

| 字段 | 格式 | 示例 |
| --- | --- | --- |
| system_date | YYYY-MM-DD | 2026-09-29 |
| system_time | HH:MM:SS（24 小时制） | 21:30:05 |

响应新增字符串字段 `screensaver_type`：`"clock"` 表示时间屏保，`"gif"` 表示 GIF 屏保。
只有启用 `s3_gif_enabled=1` 后才能选择 GIF 屏保，默认关闭。关闭时隐藏类型选项并保留原选择，但接口始终返回 `clock`；重新开启后恢复选择。已有配置缺少此开关也视为关闭。
类型未设置或非法值回退为 `clock`。该选项仅下发类型，不包含 GIF 文件或上传功能；GIF 资源与播放由 ESP32 固件处理。
两种屏保共用 `screensaver_timeout`，等待时间为 0 时均禁用。

响应还包含数字字段 `screensaver_timeout`，例如 `"screensaver_timeout":60`。
ESP32 固件应在无用户操作达到此秒数时进入屏保，0 表示禁用；正常状态轮询不应重置无操作计时。
路由器仅下发配置，不直接控制屏幕；旧 ESP32 固件需增加此字段的处理才会生效。
旧 UCI 配置缺少此选项或值非法时返回默认值 60；有前导零的合法整数会规范化为 JSON 数字。

日期和时间在采样结束后通过同一次 `date` 调用获取，使用路由器系统本地时区，避免跨午夜时日期与时间不一致。
返回的是路由器当前系统时钟，不代表已经完成 NTP 校时；ESP32 可以直接显示这两个字段。

接口条目包含 name、download_bps、upload_bps、download_bytes、upload_bytes。
bps 字段为了兼容保留命名，实际单位为 **B/s**，根据两次采样的实际时间差计算；累计计数为字节。
没有温度驱动不影响其他指标。设备消失会在下一次快照中排除，计数回退或新出现的设备速率按 0 处理。
接口仅接受 GET（405）；令牌错误返回 401；禁用返回 503；临时文件创建失败返回 500。
配置文件作为 conffile 保留，初始化不会覆盖已有非空令牌。

## 开发验证

`python3 tests/test_cgi.py` 使用 BusyBox ash 和隔离的 proc/sys/UCI 替身验证 CGI，不修改宿主配置。
LuCI 页面及实际 uHTTPd 请求头转交仍需在目标路由器上联调。
