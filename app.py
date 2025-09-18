#!/usr/bin/env python3
"""
标准HTTP代理服务器 - 自动轮换节点（修复YAML错误）
"""
import os
import base64
import yaml
import time
import subprocess
import threading
import re
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
        
    def fix_yaml_syntax(self, yaml_content):
        """修复YAML语法错误"""
        lines = yaml_content.split('\n')
        fixed_lines = []
        
        for line in lines:
            # 修复未闭合的GEOIP规则引号
            if "- 'GEOIP,CN" in line and not line.rstrip().endswith("'"):
                line = line.rstrip() + ",DIRECT'"
            # 修复其他可能的引号问题
            elif line.strip().startswith("- '") and line.count("'") % 2 != 0:
                line = line.rstrip() + "'"
            fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
        
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
        
        # 修复YAML语法
        yaml_config = self.fix_yaml_syntax(yaml_config)
        
        try:
            self.config = yaml.safe_load(yaml_config)
            
            proxies = self.config.get('proxies', [])
            for proxy in proxies:
                name = proxy.get('name', '')
                if not any(k in name for k in ['剩余流量', '距离下次', '套餐到期', '官网']):
                    self.nodes.append(proxy)
            
            print(f"✅ 加载了 {len(self.nodes)} 个节点")
            
            # 配置Clash
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
            print(f"✅ Clash Meta启动成功 (端口: {self.clash_port})")
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

# 处理代理请求
@app.before_request
def handle_proxy():
    """拦截代理请求"""
    # API端点不拦截
    if request.path in ['/', '/health', '/switch', '/nodes']:
        return None
    
    # 自动切换节点
    service.should_switch()
    
    # 处理CONNECT
    if request.method == 'CONNECT':
        return Response("HTTP/1.1 200 Connection Established\r\n\r\n", status=200)
    
    # 处理HTTP请求
    url = request.url
    if not url.startswith('http'):
        host = request.headers.get('Host')
        if host:
            url = f"http://{host}{request.path}"
            if request.query_string:
                url += '?' + request.query_string.decode()
    
    # 准备请求
    headers = {}
    for key, value in request.headers:
        if key.lower() not in ['host', 'connection', 'proxy-connection']:
            headers[key] = value
    
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
            timeout=30
        )
        
        return Response(
            resp.content,
            status=resp.status_code,
            headers=dict(resp.headers)
        )
        
    except Exception as e:
        print(f"代理错误: {e}")
        service.switch_to_next_node()
        return Response(f"Proxy Error", status=502)

@app.route('/')
def home():
    """主页"""
    return jsonify({
        'service': '标准HTTP代理',
        'type': 'HTTP/HTTPS Proxy',
        'port': os.getenv("PORT", 8080),
        'nodes': len(service.nodes),
        'current': service.current_node['name'] if service.current_node else None,
        'switch': f'每{service.switch_interval}个请求'
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'nodes': len(service.nodes)})

@app.route('/switch')
def switch():
    node = service.switch_to_next_node()
    return jsonify({'switched_to': node})

@app.route('/nodes')
def nodes():
    return jsonify({'total': len(service.nodes), 'nodes': [n['name'] for n in service.nodes]})

if __name__ == '__main__':
    if service.load_config():
        if service.start_clash():
            print("="*50)
            print("🚀 标准HTTP代理服务器就绪")
            print(f"📍 端口: {os.getenv('PORT', 8080)}")
            print("="*50)
    
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)
