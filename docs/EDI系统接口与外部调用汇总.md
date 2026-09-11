# EDI 系统接口与外部调用汇总

> 文档版本：汇总第一版  
> 负责人：陈传昔  
> 更新时间：2026-07-07  
> 适用范围：EDI-MMS、EDI 客户端 gRPC、turbocharts_app、TR 仿真数据手册生成工具、仿真助手及 DeepSeek API 依赖。

### 基础环境信息

| 项目 | 当前信息 |
|---|---|
| EDI-MMS Web前端 | `http://192.168.0.164:8080` |
| EDI-MMS REST API | `http://192.168.0.164:8000`（根据当前实测请求） |
| EDI客户端安装包版本 | `EDI_cloud_InstallerV1.0.2测试版20260706.exe` |
| EDI安装路径 | `C:\Program Files (x86)\EDI` |
| ExternalCall gRPC地址 | `127.0.0.1:50055` |
| turbocharts路径 | `C:\Program Files (x86)\EDI\turbocharts_app.exe` |
| TR Simulator路径 | `C:\Program Files (x86)\EDI\simulation_report\TR_Simulator.exe` |

## 1. 文档说明

### 1.1 验证等级

| 标记 | 含义 |
|---|---|
| 已实测 | 已对实际运行环境发送请求或执行过业务验证 |
| 部分实测 | 主流程已验证，仍有权限、异常或状态分支未覆盖 |
| 静态核对 | 已核对接口文档、协议文件、脚本或命令参数，未实际运行完整业务 |
| 待确认 | 当前资料不足、文档与实现不一致或需开发确认 |

本文件汇总 EDI 系统当前对外接口、调用字段、响应示例、验证状态及待确认事项，可作为独立交付文档使用。

### 1.2 鉴权与通用约定

- EDI-MMS REST API 基础路径：`/api/v1/`。
- 需要登录的接口使用 `Authorization: Bearer <access_token>`。
- 目标普通 JSON 响应结构为 `code/message/data`，但现有接口尚未全部统一。
- 文件下载成功时返回二进制流；`204 No Content` 可保留空响应。
- gRPC 默认资料地址为 `127.0.0.1:50055`，以客户端实际配置为准。

## 2. EDI-MMS REST API

验证状态：核心接口已完成多轮测试；少量接口与边界分支待验证。

### 2.1 用户认证

| 方法 | 路径 | 说明 | 验证状态 |
|---|---|---|---|
| POST | `/api/v1/auth/users/send-code/` | 发送注册或重置验证码 | 已实测 |
| POST | `/api/v1/auth/users/register/` | 用户注册 | 已实测 |
| POST | `/api/v1/auth/users/login/` | 用户登录并返回 access token | 已实测 |
| GET | `/api/v1/auth/users/me/` | 获取当前用户资料 | 已实测 |
| PATCH | `/api/v1/auth/users/me/` | 修改当前用户资料 | 已实测 |
| POST | `/api/v1/auth/users/change-password/` | 修改密码 | 已实测 |
| POST | `/api/v1/auth/users/reset-password/` | 重置密码 | 已实测 |
| POST | `/api/v1/auth/users/logout/` | 安全退出 | 已实测 |
| DELETE | `/api/v1/auth/admin/users/{user_id}/` | 管理员强制删除用户 | 待验证 |

### 2.2 产品分类与参数

| 资源 | 主要路径 | 支持操作 | 验证状态 |
|---|---|---|---|
| 一级分类 | `/api/v1/categories/level1/` | 列表、详情、POST、PUT、PATCH、DELETE | 已实测 |
| 二级分类 | `/api/v1/categories/level2/` | 列表、详情、POST、PUT、PATCH、DELETE | 已实测 |
| 参数分组 | `/api/v1/parameter-groups/` | 列表、详情、POST、PUT、PATCH、DELETE | 已实测 |
| 参数模板 | `/api/v1/parameter-templates/` | 列表、详情、POST、PUT、PATCH、DELETE | 已实测 |
| 单位变更 | `/api/v1/parameter-templates/apply-unit-change/` | 应用模板单位变更 | 已实测 |
| 分组参数 | `/api/v1/parameter-templates/group_params/` | 按模型子类获取模板参数 | 已实测 |

### 2.3 模型上传与管理

| 方法 | 路径 | 说明 | 验证状态 |
|---|---|---|---|
| POST | `/api/v1/file_service/upload/chunked/` | 模型包分片上传 | 已实测；Web 上传存在待定位问题 |
| POST | `/api/v1/file_service/upload/process/` | 处理已上传模型包 | 已实测 |
| POST | `/api/v1/file_service/upload/confirm-overwrite/` | 确认覆盖重复模型 | 部分实测 |
| POST | `/api/v1/file_service/upload/cancel-overwrite/` | 取消覆盖 | 待验证 |
| GET | `/api/v1/models/manage/` | 我的模型库及组合筛选 | 已实测 |
| GET | `/api/v1/models/manage/public_list/` | 公共模型库 | 已实测 |
| GET | `/api/v1/models/manage/review_list/` | 审批列表 | 已实测 |
| POST | `/api/v1/models/manage/{id}/publish/` | 发布模型 | 已实测 |
| POST | `/api/v1/models/manage/{id}/approve/` | 审批或拒绝模型 | 已实测 |
| POST | `/api/v1/models/manage/{id}/off-shelf/` | 下架模型 | 已实测 |
| GET | `/api/v1/models/manage/{id}/download/` | 下载模型 | 已实测 |
| POST | `/api/v1/models/manage/logic_delete/` | 逻辑删除模型 | 已实测 |
| GET | `/api/v1/models/manage/trash-list/` | 回收站列表 | 已实测 |
| POST | `/api/v1/models/manage/{id}/recover/` | 恢复模型 | 已实测 |
| POST | `/api/v1/models/manage/hard-delete/` | 彻底删除模型 | 已实测 |
| POST | `/api/v1/models/manage/batch-publish/` | 批量发布 | 已实测 |
| POST | `/api/v1/models/manage/batch-approve/` | 批量审批 | 已实测 |
| POST | `/api/v1/models/manage/batch-off-shelf/` | 批量下架 | 已实测 |

### 2.4 模型收藏、分享与政策

| 方法 | 路径 | 说明 | 验证状态 |
|---|---|---|---|
| GET | `/api/v1/models/favorites/` | 收藏列表 | 已实测 |
| POST/DELETE | `/api/v1/models/favorites/manage/` | 批量收藏/取消收藏 | 已实测 |
| POST | `/api/v1/models/favorites/cleanup/` | 清理无效收藏 | 已实测 |
| POST | `/api/v1/models/manage/{id}/share/` | 生成模型分享链接 | 已实测；状态校验仍有问题 |
| GET | `/api/v1/models/share/public/model/{share_id}/` | 匿名访问分享文件 | 已实测 |
| GET | `/api/v1/file_service/legal-policy/current/` | 当前法律/隐私政策 | 已实测 |
| GET | `/api/v1/file_service/policy/check/` | 查询用户签署状态 | 已实测 |
| POST | `/api/v1/file_service/policy/agree/` | 签署政策 | 已实测 |
| POST | `/api/v1/file_service/legal-policy/upload/` | 上传政策 | 已实测 |

### 2.5 供应商名称

| 方法 | 路径 | 说明 | 验证状态 |
|---|---|---|---|
| GET/POST | `/api/v1/manufacturer-names/` | 查询和新增供应商 | 已实测 |
| GET/PUT/PATCH/DELETE | `/api/v1/manufacturer-names/{id}/` | 详情、修改和删除 | 已实测 |
| GET | `/api/v1/manufacturer-names/options/` | 供应商下拉选项 | 已实测 |
| GET | `/api/v1/manufacturer-names/is-used/` | 检查供应商是否被使用 | 已实测 |

### 2.6 原理图管理

基础路径：`/api/v1/schematic_manager/`。

| 资源/动作 | 路径概要 | 验证状态 |
|---|---|---|
| 原理图 CRUD | `schematic-manager/`、`schematic-manager/{id}/` | 已实测 |
| 上传与下载 | `schematic-manager/`、`{id}/download/` | 已实测，多个下载异常仍待修复 |
| 参数上传 | `{id}/upload_params/` | 已实测，缺字段和无效关联存在问题 |
| 个人搜索 | `schematic-manager/search-owner/` | 已实测 |
| 公共搜索 | `schematic-manager/search-public/` | 已实测 |
| 待审批搜索 | `schematic-manager/search-pending/` | 已实测 |
| 回收站搜索 | `schematic-manager/search-delete/` | 已实测 |
| 发布/审批/下架 | `publish/`、`approve/`、`off_sheet/` | 已实测 |
| 删除/恢复 | `logic_delete/`、`restore/` | 已实测 |
| 原理图分类 | `schematic-categories/` | 已实测 |
| 原理图参数 | `schematic-params/` | 已实测 |
| 分类参数关系 | `replace_params/`、`get_all_params/` | 已实测 |
| 原理图收藏 | `schematic-user-favorites/` | 已实测，存在对象归属问题 |
| 原理图分享 | `schematic-manager/{id}/share/` | 已实测 |

### 2.7 REST API 详细调用说明

以下内容描述当前部署版本的可调用契约。字段标记“无”表示不需要业务请求体，但仍须携带鉴权头。

#### 2.7.1 通用请求与响应

登录后请求头：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

常见分页查询字段：`page`（页码）、`page_size`（每页数量）。当前多数列表直接返回分页对象：

```json
{
  "count": 1,
  "total_pages": 1,
  "current_page": 1,
  "results": []
}
```

标准鉴权失败示例：

```json
{
  "code": 401,
  "message": "认证失败，请提供有效的认证凭据",
  "data": null
}
```

资源删除成功通常返回 `204 No Content`；下载成功返回二进制文件，不返回 JSON。

#### 2.7.2 用户认证接口

| 接口 | 权限 | 请求字段 | 成功响应关键字段 |
|---|---|---|---|
| `POST /auth/users/send-code/` | 无 | `email:string`、`scene:reg/reset` | `code`、`message`、`data` |
| `POST /auth/users/register/` | 无 | `username`、`email`、`code`、`company`、`password`、`password2` | `data.access`、`data.user` |
| `POST /auth/users/login/` | 无 | `identity`、`password` | `data.access`、`data.user` |
| `GET /auth/users/me/` | 登录 | 无 | 用户ID、用户名、邮箱、角色、公司等 |
| `PATCH /auth/users/me/` | 登录 | 需要修改的用户资料字段 | 更新后的用户对象 |
| `POST /auth/users/change-password/` | 登录 | `old_password`、`new_password` | 修改结果消息 |
| `POST /auth/users/reset-password/` | 无 | `email`、`code`、`new_password` | 重置结果消息 |
| `POST /auth/users/logout/` | 登录 | 当前单Token版本是否需要body以部署实现为准 | 退出结果消息 |
| `DELETE /auth/admin/users/{user_id}/` | 管理员 | 路径参数`user_id:UUID` | 删除结果；尚未实测 |

登录成功示例：

```json
{
  "code": 200,
  "message": "登录成功",
  "data": {
    "access": "<access_token>",
    "user": {
      "id": "<user_uuid>",
      "username": "ccx001",
      "role": "user",
      "company": "star"
    }
  }
}
```

#### 2.7.3 产品分类与参数接口

资源字段：

| 资源 | 创建/修改字段 | 主要响应字段 |
|---|---|---|
| 一级分类 | `name`、`group_name`、`description` | `id`、`category_id`、`name`、`group_name`、`description`、`sort_order`、时间字段 |
| 二级分类 | `name`、`parent`、`param_group`、`description` | `id`、`category_id`、`parent`、`parent_name`、`param_group`、`name`、`sort_order` |
| 参数分组 | `group_name`、`description` | `id`、`group_name`、`description`、时间字段 |
| 参数模板 | `param_name`、`param_key`、`param_group_id`、`data_type`、`value_type`、`Unit`、`OptionalUnits`、`is_required`、`validation_rules`、`sort_order`、`description` | 请求字段及`id`、`group_name` |

每类资源均提供：

| 操作 | Method与路径 | 权限 | 响应 |
|---|---|---|---|
| 列表 | `GET /{resource}/?page=1&page_size=15` | 登录 | `200`分页对象 |
| 创建 | `POST /{resource}/` | 管理员 | `201`资源对象 |
| 详情 | `GET /{resource}/{id}/` | 登录 | `200`资源对象 |
| 完整修改 | `PUT /{resource}/{id}/` | 管理员 | `200`更新后对象 |
| 部分修改 | `PATCH /{resource}/{id}/` | 管理员 | `200`更新后对象 |
| 删除 | `DELETE /{resource}/{id}/` | 管理员 | `204`空响应，当前多为软删除 |

资源路径：`categories/level1`、`categories/level2`、`parameter-groups`、`parameter-templates`。

一级分类创建示例：

```json
{
  "name": "低噪声放大器",
  "group_name": "拟合模型",
  "description": "一级分类说明"
}
```

参数模板创建示例：

```json
{
  "param_name": "增益",
  "param_key": "gain",
  "param_group_id": "<group_uuid>",
  "data_type": "Max",
  "value_type": "float",
  "Unit": "dB",
  "OptionalUnits": "dB",
  "is_required": false,
  "validation_rules": "",
  "sort_order": 10,
  "description": ""
}
```

`POST /parameter-templates/apply-unit-change/` 用于应用单位变更，核心字段为模板`id`及目标单位；`GET /parameter-templates/group_params/?model_sub_type_id=<id>`按模型子类返回参数定义，关键响应为`count`、`group_id`和`data[]`。

#### 2.7.4 模型上传与状态接口

| 接口 | 权限 | 请求字段 | 成功响应/结果 |
|---|---|---|---|
| `POST /file_service/upload/chunked/` | 登录 | multipart：`file`、`file_uuid`、`chunk_index`、`total_chunks`、`file_name` | 分片接收状态、`is_all_uploaded` |
| `POST /file_service/upload/process/` | 登录 | JSON：`file_uuid` | `summary`、`results[]`；可能返回覆盖冲突临时ID |
| `POST /file_service/upload/confirm-overwrite/` | 登录 | `temp_model_id`、`target_model_id` | 覆盖处理结果 |
| `POST /file_service/upload/cancel-overwrite/` | 登录 | 临时模型标识，具体字段待实测确认 | 取消结果；尚未实测 |
| `GET /models/manage/` | 登录 | Query：分页、`sub_type`及动态参数筛选 | 当前用户模型分页对象 |
| `GET /models/manage/public_list/` | 登录 | Query：分页、分类、供应商和动态参数 | 公开且已发布模型分页对象 |
| `GET /models/manage/review_list/` | 管理员 | Query：分页 | 待审批模型分页对象 |
| `POST /models/manage/{id}/publish/` | 所有者 | 无 | `draft -> pending` |
| `POST /models/manage/{id}/approve/` | 管理员 | `action:pass/reject`等审批动作 | 通过后`published/is_public=true` |
| `POST /models/manage/{id}/off-shelf/` | 管理员/按业务权限 | 无 | `published -> draft`、`is_public=false` |
| `GET /models/manage/{id}/download/` | 有权用户 | 无 | 二进制模型文件 |
| `POST /models/manage/logic_delete/` | 所有者 | `id` | 移入回收站 |
| `GET /models/manage/trash-list/` | 登录 | Query：分页 | 当前用户已删除模型 |
| `POST /models/manage/{id}/recover/` | 所有者 | 无 | 恢复为私有`draft` |
| `POST /models/manage/hard-delete/` | 所有者 | `ids:UUID[]` | 批量物理删除结果 |
| `POST /models/manage/batch-publish/` | 所有者 | `ids:UUID[]` | 成功/失败数量和列表 |
| `POST /models/manage/batch-approve/` | 管理员 | `ids:UUID[]`、`action` | `success_ids`、`failed_list` |
| `POST /models/manage/batch-off-shelf/` | 管理员 | `ids:UUID[]` | `success_ids`、`failed` |

模型列表响应对象常见字段：

```json
{
  "id": "<model_uuid>",
  "owner_name": "admin123",
  "library_name": "LNA.V.1.0",
  "model_name": "MH1045PQ3",
  "version": "1",
  "category_name": "低噪声放大器",
  "parameters": {},
  "download_url": "/api/v1/models/manage/<id>/download/",
  "status": "published",
  "is_public": true,
  "manufacturer": "供应商名称"
}
```

批量发布请求与响应示例：

```json
{"ids":["<model_uuid_1>","<model_uuid_2>"]}
```

```json
{
  "message": "批量操作完成",
  "success_count": 2,
  "failed_count": 0,
  "success_list": [],
  "failed_list": []
}
```

#### 2.7.5 模型收藏、分享及政策接口

| 接口 | 请求字段 | 响应关键字段 |
|---|---|---|
| `GET /models/favorites/` | Query：`page`、`page_size`、`model_name`、`library_name`、`category_name`、`category_parent_name` | 收藏分页对象 |
| `POST /models/favorites/manage/` | `model_uuids:UUID[]` | `success_ids`、`already_favorited_ids`、`offline_ids`、`invalid_ids` |
| `DELETE /models/favorites/manage/` | `model_uuids:UUID[]` | `success_ids`、`not_favorited_ids`、`offline_ids`、`invalid_ids` |
| `POST /models/favorites/cleanup/` | 无 | `deleted_count`、`removed_model_ids` |
| `POST /models/manage/{id}/share/` | 无 | `share_id`、`share_url`、`model_name` |
| `GET /models/share/public/model/{share_id}/` | 无鉴权 | 二进制模型文件；无效ID返回404 |
| `GET /file_service/legal-policy/current/` | Query：`legal_policy_type` | 当前PDF/政策文件 |
| `GET /file_service/policy/check/` | Query：`legal_policy_type` | `is_agreed`、`policy_id`、`version` |
| `POST /file_service/policy/agree/` | Query：`legal_policy_type` | 签署结果、政策ID和版本 |
| `POST /file_service/legal-policy/upload/` | 管理员；multipart：`file`、`legal_policy_type`、`version` | 政策ID、文件URL、MD5、生效时间 |

分享成功示例：

```json
{
  "detail": "本地分享链接已生成",
  "share_id": "<share_uuid>",
  "share_url": "http://<host>/api/v1/models/share/public/model/<share_uuid>/",
  "model_name": "MH1045PQ3"
}
```

#### 2.7.6 供应商接口

供应商对象字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 供应商记录ID |
| `canonical_name` | string | 标准名称，最长100字符 |
| `aliases` | array[string] | 别名数组；不允许空字符串、同记录重复或跨记录重复 |
| `aliases_for_show` | string/null | 下拉框展示名称；空值回退规则待确认 |

| 接口 | 权限 | 输入 | 响应 |
|---|---|---|---|
| `GET /manufacturer-names/` | 登录 | `page`、`page_size`、`search` | 分页供应商对象 |
| `POST /manufacturer-names/` | 管理员 | 供应商对象字段 | `201`新对象 |
| `GET /manufacturer-names/{id}/` | 登录 | 路径ID | `200`对象 |
| `PUT/PATCH /manufacturer-names/{id}/` | 管理员 | 全部/部分字段 | `200`更新对象 |
| `DELETE /manufacturer-names/{id}/` | 管理员 | 路径ID | `204` |
| `GET /manufacturer-names/options/` | 登录 | 无 | `{供应商ID:展示名称}`映射 |
| `GET /manufacturer-names/is-used/` | 登录 | `manufacturer_id` | `manufacturer_id`、`is_used` |

创建示例：

```json
{
  "canonical_name": "供应商标准名称",
  "aliases": ["供应商别名"],
  "aliases_for_show": "展示名称"
}
```

#### 2.7.7 原理图资源接口

原理图核心字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 数据库主键，状态操作使用该ID |
| `schematic_id` | UUID | ZIP内部原理图业务ID |
| `name` | string | 原理图名称 |
| `properties` | object | 参数值对象 |
| `properties_name` | array | 参数展示定义和值 |
| `description` | string | 描述 |
| `category_id` | UUID/null | 原理图分类ID |
| `status` | string | `draft/pending/published/deleted` |

| 接口 | 权限/输入 | 响应 |
|---|---|---|
| `GET /schematic_manager/schematic-manager/` | 登录；分页 | 原理图分页对象 |
| `POST /schematic_manager/schematic-manager/` | 登录；multipart `file`，可附带描述、分类、状态等字段 | `201`原理图对象 |
| `GET /schematic-manager/{id}/` | 有权用户 | 原理图对象 |
| `PUT/PATCH /schematic-manager/{id}/` | 所有者；PATCH按最新需求应禁用 | 更新结果；PUT当前存在文件字段问题 |
| `DELETE /schematic-manager/{id}/` | 所有者 | `204`物理删除 |
| `GET /schematic-manager/{id}/download/` | 有权用户 | ZIP文件；多种权限/不存在场景仍可能错误返回500 |
| `POST /schematic-manager/{id}/upload_params/` | 所有者；`category_id`、`params[]` | 分类ID和properties |
| `GET /schematic-manager/{id}/share/` | 所有者 | `code/message/data.share_url` |
| `POST /schematic-manager/search-owner/` | 登录；筛选字段按页面需求 | 当前用户原理图分页对象 |
| `POST /schematic-manager/search-public/` | 登录；筛选字段 | published公共原理图 |
| `POST /schematic-manager/search-pending/` | 管理员 | pending原理图 |
| `POST /schematic-manager/search-delete/` | 登录 | 当前用户deleted原理图 |
| `POST /schematic-manager/publish/` | 所有者；`uuids:[数据库id]` | 批量成功/失败列表 |
| `POST /schematic-manager/approve/` | 管理员；`uuids`、`action:approve` | pending转published |
| `POST /schematic-manager/reject/` | 权限待最终确认；`uuids` | pending转draft |
| `POST /schematic-manager/off_sheet/` | 所有者/管理员；`uuids` | published转draft |
| `POST /schematic-manager/logic_delete/` | 所有者；`uuids` | 状态转deleted |
| `POST /schematic-manager/restore/` | 所有者；`uuids` | deleted转draft |

参数上传请求示例：

```json
{
  "category_id": "<category_uuid>",
  "params": [
    {"key":"operating_frequency_band_max","value":1,"unit":"KHz"}
  ]
}
```

批量状态响应示例：

```json
{
  "success": true,
  "message": "全部处理成功",
  "success_count": 1,
  "failed_count": 0,
  "success_list": [{"uuid":"<schematic_id>","status":"pending"}],
  "failed_list": []
}
```

#### 2.7.8 原理图分类、参数与收藏接口

| 资源 | 接口 | 主要输入字段 | 响应 |
|---|---|---|---|
| 分类 | `GET/POST /schematic-categories/` | `name`、`params_list` | 分页/分类对象 |
| 分类 | `GET/PUT/PATCH/DELETE /schematic-categories/{id}/` | 全部或部分分类字段 | 对象或204 |
| 分类参数 | `POST /schematic-categories/{id}/update_params/` | `name`、`params:[{param_key}]` | `code/message/data` |
| 全部分类参数 | `GET /schematic-categories/get_all_params/` | 无 | 分类及其参数数组 |
| 参数 | `GET/POST /schematic-params/` | `name`、`key`、`type`、`select_unit`、`unit_list`、`select_list` | 分页/参数对象 |
| 参数 | `GET/PUT/PATCH/DELETE /schematic-params/{id}/` | 全部或部分参数字段 | 对象或204 |
| 收藏 | `GET/POST /schematic-user-favorites/` | `user_id`、`schematic_id` | 分页/收藏对象 |
| 收藏 | `GET/PUT/PATCH/DELETE /schematic-user-favorites/{id}/` | 全部或部分收藏字段 | 对象或204 |

当前实现仍存在普通用户维护全局分类参数、收藏归属可被客户端篡改、无效关联可写入等问题；调用方不能把当前可写行为理解为正式权限规则。

### 2.8 暂不纳入本次验证

按当前任务要求，以下接口仅保留资料，不安排本轮验证：

- OTA 发布：`POST /api/v1/file_service/upload/process_ota/`
- 心跳：`GET /api/v1/heartbeat/`
- 文档管理有效路径

## 3. EDI 客户端 gRPC

验证状态：已连接最新 EDI 客户端 `127.0.0.1:50055` 进行最小实测。

本次实测结果：

| 动作 | PerformAction | 最终事件 | 结果/输出 |
|---|---|---|---|
| OPEN_PROJECT | `code=0 / task accepted` | `RESULT_STATUS_SUCCESS / project opened` | 通过 |
| VIEW_PROJECT_NETLIST | `code=0 / task accepted` | `RESULT_STATUS_SUCCESS / netlist generated` | 生成 `EDI_TEST/netlist.log` |
| CAPTURE_SCHEMATIC | `code=0 / task accepted` | `RUNNING -> SUCCESS / schematic captured` | 生成 `schematic.png`，49,077字节 |
| SIMULATE_PROJECT | `code=0 / task accepted` | `RESULT_STATUS_FAILED / simulation failed` | gRPC通信正常，仿真业务失败，待与客户端手工仿真对照 |
| MODEL_REPLACE | `code=0 / task accepted` | `RUNNING -> FAILED / model download failed` | 已执行；模型下载阶段失败，工程7个文件哈希均未变化 |
| CLOSE_PROJECT | `code=0 / task accepted` | `RUNNING -> SUCCESS / project closed` | 通过，`need_save=false` |

补充发现：示例脚本直接在默认 GBK 控制台输出 `✓` 会触发 `UnicodeEncodeError`；设置 `$env:PYTHONIOENCODING='utf-8'` 后正常，不属于 gRPC 接口失败。

### 3.1 服务定义

```proto
service ExternalCall {
  rpc PerformAction(Request) returns (Response);
  rpc FetchEvent(FetchEventRequest) returns (stream Event);
}
```

- `PerformAction`：提交任务。`code == 0` 仅表示任务已受理。
- `FetchEvent`：按 `client_uuid` 流式获取运行状态和最终结果。
- 常见状态：`RUNNING`、`SUCCESS`、`FAILED`。

### 3.2 Request 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| client_uuid | string | 客户端会话标识，提交和取事件时保持一致 |
| task_id | string | 每次任务的唯一标识 |
| type | EventType | 任务类型 |
| payload_json | string | JSON 对象序列化后的字符串 |

### 3.3 EventType 与 payload

| EventType | 功能 | payload_json 主要字段 |
|---|---|---|
| OPEN_PROJECT | 打开工程 | `project_path` |
| SIMULATE_PROJECT | 执行工程仿真 | `project_path`、`log_source` |
| VIEW_PROJECT_NETLIST | 获取工程网表 | `project_path` |
| MODEL_REPLACE | 根据 CSV 替换模型 | `project_path`、`csv_path` |
| CAPTURE_SCHEMATIC | 截取原理图 | `project_path`、`img_path` |
| CLOSE_PROJECT | 关闭工程 | `project_path`、`need_save` |
| CALL_SIMULATION_CONTROLLER | 调用仿真控制器 | `netlist_path`、`ads_path` |
| SEARCH_SOFT_IP_CATEGORIES | 查询软 IP 分类 | 无业务参数 |
| SEARCH_PUBLIC_SOFT_IP_MODELS | 查询公共软 IP 模型 | `filters` |
| SEARCH_PERSONAL_SOFT_IP_MODELS | 查询个人软 IP 模型 | `filters` |
| DOWNLOAD_SOFT_IP_MODEL | 下载软 IP AEDT 模型 | `id`、`save_path`、`freq` |

默认资料地址：`localhost:50055`。

Python 示例入口：

```powershell
cd "EDI客户端软件调试\code\grpc-test"
python grpc_test.py --server localhost:50055
```

调用前必须确认 EDI 客户端已启动、gRPC 端口正确，并将脚本中的工程、CSV 和图片路径替换为本机真实路径。

## 4. turbocharts_app 命令行调用

验证状态：已找到并真实运行 `C:\Program Files (x86)\EDI\turbocharts_app.exe`。

文件信息：大小25,088字节，文件时间2026-07-06 20:49:10；未写入Windows文件版本和产品版本。

实测结果：

| 场景 | 输入 | 结果 |
|---|---|---|
| `--help` | 无业务参数 | 能输出参数帮助，但随后提示缺少`--raw`并返回`-1` |
| `--version` | 无业务参数 | 未输出版本号，仍显示帮助并返回`-1` |
| S参数转图 | 真实`result.raw`、`DB_S[2,1]`、依赖轴`freq` | 返回`0`，生成PNG和CSV |

真实产物：PNG 13,548字节，CSV 1,755字节；CSV表头为`freq,dB(S[21])`，包含7GHz起的有效曲线数据。

通俗理解：该工具不负责执行电路仿真，它负责读取 ADS 已经生成的 RAW 结果，把指定曲线转换为图片，并可同步导出 CSV。

```text
ADS result.raw -> turbocharts_app.exe -> 曲线图.png + 数据.csv
```

### 4.1 命令格式

```powershell
turbocharts_app.exe --raw <raw_path> --img <image_path> --type <image_type> [--csv <csv_path>] [--linename <line_name>] [--dependcy <name>] [--ac <config>]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--raw` | 是 | 输入 RAW 文件路径 |
| `--img` | 是 | 输出 PNG/JPG 等图片路径 |
| `--type` | 是 | 转换类型，如 `SP`、`HB` |
| `--csv` | 否 | 同时导出的 CSV 路径 |
| `--linename` | 否 | 曲线名；多条曲线使用 `&` 分隔 |
| `--dependcy` | 否 | 依赖轴名称，常用 `freq` |
| `--ac` | 否 | 精度配置：`ac_type#bit#data#nv_type#nv_value` |

### 4.2 命令行示例

以下示例来自现有工具使用说明，路径可替换为实际 RAW 与输出目录。

S参数增益曲线，同时输出PNG和CSV：

```powershell
turbocharts_app.exe --raw "C:\Users\crb\Desktop\中文路径测试\result_tr.raw" --csv "C:\Users\crb\Desktop\中文路径测试\输出SP_tr.csv" --img "C:\Users\crb\Desktop\中文路径测试\输出SP_tr.png" --type "SP" --linename "DB_S[2,1]" --dependcy "freq"
```

噪声系数：

```powershell
turbocharts_app.exe --raw "C:\Users\crb\Desktop\中文路径测试\result.raw" --csv "C:\Users\crb\Desktop\中文路径测试\输出SPnf.csv" --img "C:\Users\crb\Desktop\中文路径测试\输出SPnf.png" --type "SP" --linename "real_nf(1)" --dependcy "freq"
```

输入驻波：

```powershell
turbocharts_app.exe --raw "C:\Users\crb\Desktop\中文路径测试\result.raw" --csv "C:\Users\crb\Desktop\中文路径测试\输出VSWR.csv" --img "C:\Users\crb\Desktop\中文路径测试\输出VSWR.png" --type "SP" --linename "VSWR_S[1,1]" --dependcy "freq"
```

多曲线与相位精度计算：

```powershell
turbocharts_app.exe --raw "C:\Users\crb\Desktop\中文路径测试\result_S_SWEEP.raw" --csv "C:\Users\crb\Desktop\中文路径测试\输出SPS.csv" --img "C:\Users\crb\Desktop\中文路径测试\输出SPS.png" --type "SP" --linename "DB_S[2,1]&DB_S[1,1]" --dependcy "freq" --ac "phase#3#S[2,1]#fv#0.1"
```

`--ac`值由五段组成：`ac_type#bit#data#nv_type#nv_value`。`phase`表示相位精度，`att`表示衰减精度；`fv`表示固定间隔，`cl`表示完整列表。

支持的已知曲线包括：

| 曲线表达式 | 说明 |
|---|---|
| `DB_S[2,1]` | S参数增益 |
| `real_delayS[2,1]` | 群时延 |
| `real_nf(1)` | 噪声系数 |
| `VSWR_S[1,1]` | 输入驻波 |
| `APS_S[2,1]` | 衰减器附加相移 |
| `MAS_S[2,1]` | 衰减态/幅度相关结果 |
| `MV_S[2,1]` | 移相器幅度波动 |
| `PSS_S[2,1]` | 移相态 |

已确认程序参数名固定拼写为`--dependcy`。仍待确认完整`type`枚举、错误输出位置以及为何帮助和版本参数返回`-1`。

## 5. TR 仿真数据手册生成工具

本节根据当前 TR 仿真数据手册生成工具调用说明整理。

验证状态：已找到并在当前安装环境中以管理员权限启动 `C:\Program Files (x86)\EDI\simulation_report\TR_Simulator.exe`。

文件信息：大小39,690,660字节，文件时间2026-07-02 14:38:30；x64程序；SHA-256为`A33FF2B1FB8376B0E6A120FC8557D9EE881C5BADD368A396559B0E5990A4650A`；未写入Windows文件版本和产品版本，也未提供数字签名。同目录只有该EXE，未见随附DLL或配置文件。

权限与运行特征：

- 普通权限启动返回Windows初始化错误`0xc0000142`。
- 管理员权限启动不再报错。当前安装在受保护的`Program Files (x86)`目录，运行时可能需要写入或解压资源，因此当前安装方式下需提升权限；程序本身是否强制要求管理员仍需在普通可写目录验证。
- 管理员权限执行`--help`时没有控制台帮助文本和可读取退出码。
- 使用`--epp`、`--edi`、`--mode validate`和`--json`可创建独立控制台窗口执行校验。
- 已使用`EDI_TEST`工程完成真实验证：程序读取`netlist.log`和`schematics/main/schematic.ep`，确认输入端口1、输出端口2存在，最终输出`validate：符合要求`。
- validate模式不生成仿真报告，校验结论显示在独立控制台窗口中。
- PowerShell传递`--json`时必须保留JSON双引号，例如先定义`$json='[{\"name\":\"EDI_TEST_validate\",\"input_port\":1,\"output_port\":2}]'`，否则会触发`JSONDecodeError`。

通俗理解：这是上层自动化编排工具。它连接 EDI 获取网表，调用 ADS 完成仿真，再调用 turbocharts 生成图表，最后结合 DeepSeek 输出 PDF/DOCX 数据手册。

```text
EDI工程.epp -> gRPC获取网表 -> ADS仿真 -> turbocharts制图 -> DeepSeek生成文字/判定 -> PDF+DOCX
```

### 5.1 当前资料确认的调用入口

现有资料描述的是 Python 入口，并非 EXE：

```powershell
python run-simulate.py --epp <工程.epp> --edi <EDI安装目录> [其他参数]
```

| 参数 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `--epp` | 是 | 无 | EDI `.epp` 工程路径 |
| `--edi` | 是 | 无 | EDI 安装路径 |
| `--ads` | 否 | 自动判断 | ADS 安装路径 |
| `--indicators` | 否 | 工作频率、输出增益、噪声系数 | 逗号分隔的指标列表 |
| `--pdf` | 否 | 自动生成 | PDF 输出路径 |
| `--threads` | 否 | 5 | 指标仿真并发线程数 |
| `--mode` | 否 | simulate | `simulate` 或 `validate` |
| `--components` | 否 | `[]` | 器件选型 JSON 数组 |
| `--json` | 否 | `[]` | 链路参数 JSON 数组 |

### 5.2 完整调用示例

现有资料提供的真实入口是 Python 脚本：

```powershell
python run-simulate.py `
  --epp "D:\EDI\workspace\projects\NC353109ME-8084\NC353109ME-8084.epp" `
  --edi "D:\EDI" `
  --indicators "工作频率,输出增益,噪声系数" `
  --components '[{"type":"低噪声放大器","model":"NC10149C_812","manufacturer":"电科13所","specs":"8-12GHz, G>26dB, NF<1.8dB"}]' `
  --json '[{"name":"发射链路1","min_freq":6,"max_freq":12,"input_port":1,"output_port":2,"phase_step":5.5,"atten_step":0.5,"phase_devices":["NC12104C_810SD1"],"atten_devices":["NC1342C_8121"],"freq_rx":9,"pwr_rx":-20,"requirements":{"反向增益":"≥20dB","噪声系数":"≤3dB"}}]'
```

该示例表示：对指定 `.epp` 工程的“发射链路1”执行6至12GHz范围的工作频率、输出增益和噪声系数仿真，并把器件选型、仿真结果及指标判断写入数据手册。

如果交付物确实提供 `TR_Simulator.exe`，且开发确认参数与Python入口完全一致，则预期调用形式为：

```powershell
TR_Simulator.exe `
  --epp "D:\EDI\workspace\projects\NC353109ME-8084\NC353109ME-8084.epp" `
  --edi "D:\EDI" `
  --indicators "工作频率,输出增益,噪声系数" `
  --pdf "D:\output\NC353109ME-8084.pdf" `
  --threads 5 `
  --mode simulate `
  --components '[{"type":"低噪声放大器","model":"NC10149C_812","manufacturer":"电科13所","specs":"8-12GHz, G>26dB, NF<1.8dB"}]' `
  --json '[{"name":"发射链路1","min_freq":6,"max_freq":12,"input_port":1,"output_port":2,"requirements":{"输出增益":"≥20dB","噪声系数":"≤3dB"}}]'
```

注意：`TR_Simulator.exe`的`validate`模式以及`--epp/--edi/--mode/--json`参数已实测通过；完整`simulate`报告生成流程仍未验证。

### 5.3 前置条件

- Windows、Python 3.10+。
- EDI 已运行并提供 `127.0.0.1:50055` gRPC 服务。
- Keysight ADS 已安装。
- 已安装项目 `requirements.txt`。
- `.epp`、端口、器件名称和输出目录有效。

### 5.4 主要流程与输出

流程包括：获取网表、分析信号路径、修改开关状态、注入仿真配置、调用 ADS、使用 turbocharts 生成图表、调用 DeepSeek 生成简介及结果判断、输出 PDF/DOCX。

输出目录包含：

- 原始及修改后的 `netlist.log`
- 仿真 `result.raw`
- 图像 `.png`
- 数据 `.csv`
- `document.pdf`
- `document.docx`

支持 SP、CalcNoise、ParamSweep、HB、XDB 等仿真方式及工作频率、增益、噪声系数、驻波、RMS、P1dB、Psat 等指标。

### 5.5 已确认信息与剩余验证

已确认：

1. `TR_Simulator.exe`真实存在，路径为`C:\Program Files (x86)\EDI\simulation_report\TR_Simulator.exe`。
2. 当前安装位置下需以管理员权限启动。
3. `--epp`、`--edi`、`--mode validate`和`--json`参数可用。
4. `validate`可读取工程网表和原理图，完成端口校验并输出`validate：符合要求`。
5. `validate`结果显示在独立控制台窗口，不生成PDF/DOCX报告。

剩余待确认：

1. EXE参数是否与`run-simulate.py`全部一致。
2. 完整`simulate`模式能否生成RAW、PNG、CSV、PDF和DOCX。
3. 默认输出目录、日志路径和退出码。
4. 普通可写目录下是否可以不使用管理员权限。
5. `validate`模式是否完全不会调用DeepSeek。

## 6. 仿真助手与大模型 API

本节根据当前 Simulation API 调用说明及最小实测结果整理。

通俗理解：这一组接口不是直接运行 ADS，而是通过聊天帮助用户把自然语言需求整理成 TR 仿真工具可以使用的结构化参数。

```text
用户自然语言 -> init/chat多轮采集 -> confirm -> indicators + link_json -> 交给TR仿真工具
```

### 6.1 EDI-MMS 仿真助手接口

基础路径：`/api/v1/simulation/`，全部接口要求 Bearer Token。

| 方法 | 路径 | 功能 | 本次验证 |
|---|---|---|---|
| POST | `/api/v1/simulation/init/` | 创建会话并注入器件列表 | 200，通过 |
| POST | `/api/v1/simulation/chat/` | SSE 流式聊天 | 未调用 DeepSeek，待验证 |
| POST | `/api/v1/simulation/confirm/` | 生成结构化仿真参数 JSON | 未调用 DeepSeek，待验证 |
| GET | `/api/v1/simulation/history/?session_id=...` | 查询当前用户会话历史 | 200，通过 |
| DELETE | `/api/v1/simulation/history/?session_id=...` | 删除会话历史 | 200，通过 |

本次最小实测结果：

- 登录成功，未记录或输出 token。
- 初始化返回合法 `session_id`。
- 新会话历史返回空 `messages`。
- 删除返回“会话已清除”。
- 匿名初始化返回标准 `401 code/message/data:null`。
- 临时会话已删除，不保留测试数据。

### 6.2 chat SSE 契约

请求头：

```http
Content-Type: application/json
Accept: text/event-stream
Authorization: Bearer <access_token>
```

请求体：

```json
{
  "session_id": "<init返回值>",
  "message": "我要仿真工作频率、输出增益和噪声系数"
}
```

正常事件：

```text
data: {"content":"..."}

data: [DONE]
```

### 6.3 confirm 返回概要

`confirm` 根据会话历史生成：

- `data.indicators`：指标数组。
- `data.link_json`：链路参数数组。
- 常见链路字段：频率、端口、移相/衰减器件、接收频率和功率、指标要求。

### 6.4 DeepSeek 外部依赖

服务端调用：

```text
https://api.deepseek.com/chat/completions
```

配置项：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_SIMULATION_PROMPT`

本系统未向前端暴露 API Key。DeepSeek 请求失败、非 200 或内容无法解析时，仿真助手接口预计返回 `502`。这些真实依赖分支本次未触发。

### 6.5 会话规则

- 缓存 key：`sim:chat:{user_id}:{session_id}`。
- 默认有效期：24 小时。
- 不同用户会话隔离。
- 删除会话后继续 chat/confirm 应返回会话不存在或已过期。

## 7. 当前待确认与待补验证

| 优先级 | 项目 | 待确认/验证内容 |
|---|---|---|
| 中 | TR_Simulator | validate已通过；继续验证simulate报告输出、退出码和日志 |
| 高 | 仿真助手 | 验证 chat SSE、confirm JSON、用户会话隔离及删除后访问 |
| 中 | gRPC | OPEN/VIEW/CAPTURE/CLOSE已通过；继续定位SIMULATE失败和MODEL_REPLACE下载失败，补测仿真控制器 |
| 中 | turbocharts | 主流程已验证；补测缺参、错误RAW、错误曲线名及帮助参数返回码 |
| 中 | 模型覆盖 | 验证 cancel-overwrite 和非所属用户权限 |
| 中 | 管理员用户管理 | 使用一次性账号验证强制删除及关联数据处理 |
| 低 | 接口响应治理 | 逐步统一 `code/message/data`、中文提示及下载失败契约 |
