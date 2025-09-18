#!/usr/bin/env python3
"""
标准HTTP代理服务器 - 自动轮换节点
"""
import os
import base64
import yaml
import time
import subprocess
import threading
import socket
import select
from flask import Flask, request, Response, jsonify
import requests

app = Flask(__name__)

class RotatingProxyService:
    def __init__(self):
        self.nodes = []
        self.current_index = 0
        self.clash_process = None
        self.clash_port = 7890
        self.controller_port = 9090
        self.current_node = None
        self.lock = threading.Lock()
        self.request_count = 0
        self.switch_interval = int(os.getenv("SWITCH_INTERVAL", "3"))
        
    def load_config(self):
        """加载Clash配置"""
        yaml_config = os.getenv("CLASH_YAML", "")
        
        if not yaml_config:
            print("错误: 未设置CLASH_YAML")
            return False
            
        try:
            yaml_config = base64.b64decode(yaml_config).decode('utf-8')
        except:
            pass
        
        try:
            self.config = yaml.safe_load(yaml_config)
            
            proxies = self.config.get('proxies', [])
            for proxy in proxies:
                name = proxy.get('name', '')
                if not any(k in name for k in ['剩余流量', '距离下次', '套餐到期', '官网']):
                    self.nodes.append(proxy)
            
            print(f"加载了 {len(self.nodes)} 个节点")
            
            self.config['mixed-port'] = self.clash_port
            self.config['allow-lan'] = True
            self.config['bind-address'] = '0.0.0.0'
            self.config['external-controller'] = f'0.0.0.0:{self.controller_port}'
            
            if 'proxy-groups' not in self.config:
                self.config['proxy-groups'] = []
            
            self.config['proxy-groups'].insert(0, {
                'name': 'AUTO_ROTATE',
                'type': 'select',
                'proxies': [p['name'] for p in self.nodes]
            })
            
            with open('/tmp/clash_config.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True)
            
            return True
        except Exception as e:
            print(f"配置解析错误: {e}")
            return False
    
    def start_clash(self):
        """启动Clash Meta"""
        try:
            if not os.path.exists('./mihomo'):
                print("下载Clash Meta...")
                os.system('wget -q -O mihomo.gz https://github.com/MetaCubeX/mihomo/releases/download/v1.18.1/mihomo-linux-amd64-v1.18.1.gz && gunzip -f mihomo.gz && chmod +x mihomo')
            
            self.clash_process = subprocess.Popen(
                ['./mihomo', '-f', '/tmp/clash_config.yaml'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            time.sleep(5)
            print(f"Clash Meta启动成功 (端口: {self.clash_port})")
            self.switch_to_next_node()
            return True
        except Exception as e:
            print(f"启动失败: {e}")
            return False
    
    def switch_to_next_node(self):
        """切换节点"""
        if not self.nodes:
            return None
            
        with self.lock:
            self.current_node = self.nodes[self.current_index % len(self.nodes)]
            self.current_index += 1
            
            try:
                response = requests.put(
                    f'http://127.0.0.1:{self.controller_port}/proxies/AUTO_ROTATE',
                    json={'name': self.current_node['name']},
                    timeout=2
                )
                if response.status_code == 204:
                    print(f"切换到: {self.current_node['name']}")
                    return self.current_node['name']
            except Exception as e:
                print(f"切换失败: {e}")
            return None
    
    def should_switch(self):
        """判断是否需要切换节点"""
        self.request_count += 1
        if self.switch_interval > 0 and self.request_count % self.switch_interval == 0:
            self.switch_to_next_node()

service = RotatingProxyService()

# 处理标准HTTP代理请求
@app.before_request
def handle_proxy():
    """拦截所有请求作为代理处理"""
    # 自动切换节点
    service.should_switch()
    
    # 处理CONNECT方法（HTTPS隧道）
    if request.method == 'CONNECT':
        return handle_connect()
    
    # 处理普通HTTP请求
    return handle_http_request()

def handle_connect():
    """处理CONNECT请求 - HTTPS隧道"""
    return Response("HTTP/1.1 200 Connection Established\r\n\r\n", status=200)

def handle_http_request():
    """处理HTTP代理请求"""
    # 获取完整URL
    url = request.url
    
    # 如果是相对路径，构建完整URL
    if not url.startswith('http'):
        host = request.headers.get('Host')
        if host:
            url = f"http://{host}{request.path}"
            if request.query_string:
                url += '?' + request.query_string.decode()
    
    # 跳过健康检查和API端点
    if request.path in ['/', '/health', '/switch', '/nodes']:
        return None  # 让Flask继续处理
    
    # 准备请求头
    headers = {}
    for key, value in request.headers:
        if key.lower() not in ['host', 'connection', 'proxy-connection']:
            headers[key] = value
    
    # 通过Clash代理转发
    proxies = {
        'http': f'http://127.0.0.1:{service.clash_port}',
        'https': f'http://127.0.0.1:{service.clash_port}'
    }
    
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=request.get_data(),
            proxies=proxies,
            verify=False,
            allow_redirects=False,
            timeout=30,
            stream=True
        )
        
        # 构建响应
        response_headers = []
        for key, value in resp.headers.items():
            if key.lower() not in ['connection', 'transfer-encoding', 'content-encoding']:
                response_headers.append((key, value))
        
        return Response(
            resp.iter_content(chunk_size=8192),
            status=resp.status_code,
            headers=response_headers
        )
        
    except Exception as e:
        print(f"代理错误: {e}")
        service.switch_to_next_node()  # 出错时切换节点
        return Response(f"Proxy Error: {e}", status=502)

@app.route('/')
def home():
    """主页"""
    return jsonify({
        'service': '标准HTTP代理服务器',
        'proxy_type': 'HTTP/HTTPS',
        'proxy_port': os.getenv("PORT", 8080),
        'total_nodes': len(service.nodes),
        'current_node': service.current_node['name'] if service.current_node else None,
        'switch_interval': f'每{service.switch_interval}个请求切换节点',
        'usage': f'设置HTTP代理: http://{request.host}'
    })

@app.route('/health')
def health():
    """健康检查"""
    return jsonify({'status': 'healthy', 'nodes': len(service.nodes)})

@app.route('/switch')
def switch():
    """手动切换节点"""
    node = service.switch_to_next_node()
    return jsonify({'switched_to': node})

@app.route('/nodes')
def nodes():
    """查看所有节点"""
    return jsonify({
        'total': len(service.nodes),
        'nodes': [n['name'] for n in service.nodes]
    })

if __name__ == '__main__':
    if service.load_config():
        if service.start_clash():
            print("="*50)
            print("标准HTTP代理服务器就绪")
            print(f"代理地址: http://0.0.0.0:{os.getenv('PORT', 8080)}")
            print("="*50)
    
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)
