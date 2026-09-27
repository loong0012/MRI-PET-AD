"""
AD-Screen 性能压测脚本（locust）
==================================================================
模拟多用户并发访问核心只读接口，评估系统在峰值负载下的响应能力。

安装与运行：
    pip install locust
    locust -f scripts/locustfile.py --host=http://127.0.0.1:8000

启动后浏览器打开 http://localhost:8089 配置并发用户数与爬升速率。

压测场景（按权重模拟真实用户行为）：
    - 仪表盘统计（高频，权重 30）
    - 病例列表检索（中频，权重 25）
    - 病例详情查看（中频，权重 20）
    - 病例搜索（低频，权重 15）
    - 模型状态查询（低频，权重 10）

注意事项：
    - 压测会产生大量请求并写入审计日志，建议在测试环境运行
    - 并发用户数建议从 10 开始逐步加压，观察 P95 延迟与错误率
    - 后端 /api/health 可作为 liveness 探针，压测期间监控其可用性
"""
import os
import json
import random
from locust import HttpUser, task, between

# 测试账号（与 services/data_init.py 演示数据一致）
TEST_USERS = [
    {"username": "rad01", "password": "123456"},
    {"username": "neu01", "password": "123456"},
    {"username": "sci01", "password": "123456"},
]


class AdScreenUser(HttpUser):
    """模拟 AD-Screen 系统的并发用户"""

    # 请求间隔 1-3 秒，模拟人类操作停顿
    wait_time = between(1, 3)
    # 压测目标地址由 --host 参数指定

    def on_start(self):
        """用户启动时登录获取 JWT token"""
        # 随机选一个测试账号
        cred = random.choice(TEST_USERS)
        with self.client.post(
            "/api/auth/login",
            json={"username": cred["username"], "password": cred["password"]},
            name="POST /auth/login",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                token = (data.get("data") or {}).get("token")
                if token:
                    self.client.headers.update({"Authorization": f"Bearer {token}"})
                    # 预取一个病例 ID 供详情查看使用
                    self._case_id = self._fetch_first_case_id()
                    return
            # 登录失败标记为失败
            resp.failure("登录未获取到有效 token")
            self._case_id = None

    def _fetch_first_case_id(self):
        """拉取病例列表第一条 ID，供详情接口使用"""
        with self.client.get(
            "/api/case/search?keyword=&pageSize=1&page=1",
            name="GET /case/search (预热)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                items = (resp.json().get("data") or {}).get("list") or []
                if items:
                    return items[0].get("id")
        return None

    @task(30)
    def dashboard_stats(self):
        """仪表盘统计（高频轮询，前端 30s 缓存）"""
        self.client.get("/api/dashboard/stats", name="GET /dashboard/stats")

    @task(25)
    def case_query(self):
        """病例列表分页检索"""
        page = random.randint(1, 3)
        self.client.post(
            "/api/case/query",
            json={"page": page, "pageSize": 20},
            name="POST /case/query",
        )

    @task(20)
    def case_detail(self):
        """病例详情查看"""
        if not self._case_id:
            return
        self.client.get(f"/api/case/{self._case_id}", name="GET /case/{id}")

    @task(15)
    def case_search(self):
        """病例关键词搜索"""
        keyword = random.choice(["", "AD", "MCI", "AD26"])
        self.client.get(
            f"/api/case/search?keyword={keyword}&pageSize=10&page=1",
            name="GET /case/search",
        )

    @task(10)
    def model_status(self):
        """模型配置状态查询"""
        self.client.get("/api/model/status", name="GET /model/status")

    def on_stop(self):
        """用户结束清理"""
        self.client.headers.pop("Authorization", None)
