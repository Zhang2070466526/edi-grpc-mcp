# 功能说明

本工具读取 JSON 配置，将一组或多组 XY 数据绘制为曲线图并自动保存。

## 绘图能力

- 支持一组或多组曲线。
- 支持图表标题、X/Y 轴标题、图例和 Mark。
- 支持 PNG、JPG、JPEG，默认 PNG。
- 输出图片固定为 1600×1000。
- 自动生成不同颜色，并组合不同线型和标记。
- `show_window=false` 时全程后台运行；`true` 时在图片落盘后显示结果窗口。



## 运行要求

EXE 带管理员权限清单，启动时由 Windows 请求 UAC。成功退出码为 `0`；失败退出码为 `1`。



# 传参说明

调用格式：`<EXE 路径> <配置文件路径>`。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `chart_title` | string | 否 | 图表标题；空字符串表示不显示 |
| `x_axis_title` | string | 否 | X 轴标题 |
| `y_axis_title` | string | 否 | Y 轴标题 |
| `series` | array | 是 | 非空数据组数组 |
| `series[].name` | string | 否 | 缺失或为空时图例不显示；已提供名称必须唯一 |
| `series[].points` | number[][] | 是 | 非空 `[x,y]` 数组，数值必须有限 |
| `marks` | array | 否 | Mark 数组 |
| `marks[].series` | string | 是 | 必须精确匹配已有曲线名称 |
| `marks[].point` | number[2] | 是 | 必须精确匹配该曲线的数据点 |
| `marks[].label` | string | 否 | 缺失或为空时只显示 Mark 点，不显示文字 |
| `show_window` | boolean | 否 | 默认 `true`；Agent 自动化应设为 `false` |
| `image_path` | string | 是 | 输出目录路径，不含文件名；不存在时自动创建 |
| `image_format` | string | 否 | `png`、`jpg`、`jpeg`，默认 `png` |


## 输出文件

文件名使用配置文件名去掉 `.json` 后拼接扩展名。假设配置文件名为 `<配置文件名>.json`：

- PNG：`<配置文件名>.png`
- JPG：`<配置文件名>.jpg`
- JPEG：`<配置文件名>.jpeg`

## 返回 JSON

成功：

```json
{"status":"success","message":"绘图完成","image_path":"<输出目录>\\<配置文件名>.png","error_code":null}
```

失败：

```json
{"status":"error","message":"image_path 必须是目录：<输出目录>","image_path":null,"error_code":1}
```

stdout 仅一个 JSON；所有可捕获错误及原因都放在 `message`。


# 示例文档
```text
示例
```
{
  "chart_title": "三温度放大器增益曲线",
  "x_axis_title": "频率 (GHz)",
  "y_axis_title": "增益 (dB)",
  "series": [
    {
      "name": "增益-25°C",
      "points": [
        [0.1, 10.2],
        [0.25, 10.8],
        [0.5, 11.8],
        [1.0, 13.2],
        [2.0, 15.4],
        [4.0, 18.7],
        [6.0, 21.5],
        [8.0, 22.4],
        [10.0, 21.9],
        [12.0, 20.8],
        [14.0, 19.5],
        [16.0, 17.6],
        [18.0, 15.1],
        [20.0, 12.0]
      ]
    },
    {
      "name": "增益-55°C",
      "points": [
        [0.1, 9.8],
        [0.25, 10.4],
        [0.5, 11.4],
        [1.0, 12.8],
        [2.0, 15.0],
        [4.0, 18.3],
        [6.0, 21.1],
        [8.0, 22.0],
        [10.0, 21.5],
        [12.0, 20.4],
        [14.0, 19.1],
        [16.0, 17.2],
        [18.0, 14.7],
        [20.0, 11.6]
      ]
    },
    {
      "name": "增益-85°C",
      "points": [
        [0.1, 9.5],
        [0.25, 10.1],
        [0.5, 11.0],
        [1.0, 12.5],
        [2.0, 14.7],
        [4.0, 18.0],
        [6.0, 20.8],
        [8.0, 21.7],
        [10.0, 21.2],
        [12.0, 20.1],
        [14.0, 18.8],
        [16.0, 16.9],
        [18.0, 14.4],
        [20.0, 11.2]
      ]
    }
  ],
  "marks": [
    {
      "series": "增益-25°C",
      "point": [8.0, 22.4],
      "label": "25°C 峰值"
    },
    {
      "series": "增益-55°C",
      "point": [8.0, 22.0],
      "label": "55°C 峰值"
    },
    {
      "series": "增益-85°C",
      "point": [8.0, 21.7],
      "label": "85°C 峰值"
    },
    {
      "series": "增益-25°C",
      "point": [16.0, 17.6],
      "label": "高频滚降"
    }
  ],
  "show_window": true,
  "image_path": "C:\\PlotOutput",
  "image_format": "png"
}


## 管理员运行

```

.\drawtool.exe .\manual_config.json

```

启动时确认 UAC。图片落盘后显示结果窗口；关闭窗口后命令继续执行。

当前配置使用 PNG，输出：

```text
C:\PlotOutput\manual_config.png
```

返回：

```json
{"status":"success","message":"绘图完成","image_path":"C:\\PlotOutput\\manual_config.png","error_code":null}
```


## 相对路径说明

命令行中的 EXE 路径和配置文件路径支持相对于当前工作目录。配置中的 `image_path` 是输出目录，目前必须填写绝对路径。




