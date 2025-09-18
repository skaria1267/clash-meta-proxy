#!/usr/bin/env python3
"""节点轮换代理服务"""
import os
import base64
import yaml
import json
import time
import threading
import subprocess
from flask import Flask, request, Response, jsonify
import requests
from urllib.parse import urlparse

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
        
    def load_config(self):
        """加载并解析Clash配置"""
        yaml_config = os.getenv("CLASH_YAML", "")
        
        if not yaml_config:
            print("错误: 未设置CLASH_YAML环境变量")
            return False
            
        # 解码Base64
        try:
            yaml_config = base64.b64decode(yaml_config).decode('utf-8')
        except:
            pass
        
        # 修复YAML语法错误 - 处理未闭合的引号
        lines = yaml_config.split('\n')
        fixed_lines = []
        
        for i, line in enumerate(lines):
            # 检查是否包含未闭合的引号
            if line.strip().startswith("- 'GEOIP,CN") and not line.strip().endswith("'"):
                # 添加闭合引号和后续内容
                fixed_lines.append(line + ",DIRECT'")
            else:
                fixed_lines.append(line)
        
        yaml_config = '\n'.join(fixed_lines)
        
        try:
            self.config = yaml.safe_load(yaml_config)
            
            # 提取所有可用节点
            proxies = self.config.get('proxies', [])
            for proxy in proxies:
                name = proxy.get('name', '')
                # 跳过信息节点
                if not any(k in name for k in ['剩余流量', '距离下次', '套餐到期', '官网']):
                    self.nodes.append(proxy)
            
            print(f"加载了 {len(self.nodes)} 个可用节点")
            
            # 修改配置
            self.config['mixed-port'] = self.clash_port
            self.config['allow-lan'] = True
            self.config['bind-address'] = '0.0.0.0'
            self.config['external-controller'] = f'0.0.0.0:{self.controller_port}'
            self.config['secret'] = ''
            
            # 创建轮换代理组
            if 'proxy-groups' not in self.config:
                self.config['proxy-groups'] = []
            
            self.config['proxy-groups'].insert(0, {
                'name': 'AUTO_ROTATE',
                'type': 'select',
                'proxies': [p['name'] for p in self.nodes]
            })
            
            # 保存配置
            with open('/tmp/clash_config.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True)
            
            return True
            
        except Exception as e:
            print(f"配置解析错误: {e}")
            return False
    
    def start_clash(self):
        """启动Clash Meta"""
        try:
            # 下载Clash Meta
            if not os.path.exists('./mihomo'):
                print("下载Clash Meta...")
                os.system('wget -q -O mihomo.gz https://github.com/MetaCubeX/mihomo/releases/download/v1.18.1/mihomo-linux-amd64-v1.18.1.gz && gunzip -f mihomo.gz && chmod +x mihomo')
            
            # 启动进程
            self.clash_process = subprocess.Popen(
                ['./mihomo', '-f', '/tmp/clash_config.yaml'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            time.sleep(5)
            print(f"Clash Meta启动成功 (端口: {self.clash_port})")
            
            # 设置初始节点
            self.switch_to_next_node()
            return True
            
        except Exception as e:
            print(f"启动Clash失败: {e}")
            return False
    
    def switch_to_next_node(self):
        """切换到下一个节点"""
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
                    print(f"切换到节点: {self.current_node['name']}")
                    return self.current_node['name']
                    
            except Exception as e:
                print(f"切换节点失败: {e}")
            
            return None
    
    def get_proxy_url(self):
        """获取当前代理URL"""
        return {
            'http': f'http://127.0.0.1:{self.clash_port}',
            'https': f'http://127.0.0.1:{self.clash_port}'
        }

service = RotatingProxyService()
request_count = 0
switch_interval = int(os.getenv("SWITCH_INTERVAL", "1"))

@app.route('/proxy', methods=['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'])
def proxy():
    """代理端点 - 自动轮换节点"""
    global request_count
    
    request_count += 1
    if request_count % switch_interval == 0:
        service.switch_to_next_node()
    
    target_url = request.args.get('url')
    if not target_url:
        return jsonify({'error': '缺少url参数'}), 400
    
    try:
        headers = {k: v for k, v in request.headers if k.lower() not in ['host', 'content-length']}
        
        response = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=request.get_data(),
            proxies=service.get_proxy_url(),
            verify=False,
            allow_redirects=False,
            timeout=30
        )
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except requests.exceptions.RequestException as e:
        # 如果请求失败，自动切换节点并重试
        service.switch_to_next_node()
        return jsonify({'error': str(e), 'action': 'switched_node'}), 500

@app.route('/')
def home():
    """主页"""
    host = request.headers.get('Host', 'localhost').split(':')[0]
    return jsonify({
        'service': '节点轮换代理',
        'total_nodes': len(service.nodes),
        'current_node': service.current_node['name'] if service.current_node else None,
        'proxy_endpoint': f'http://{host}:{os.getenv("PORT", 8080)}/proxy?url=目标URL',
        'switch_interval': f'每{switch_interval}个请求切换节点',
        'raw_proxy': f'http://{host}:{service.clash_port}'
    })

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

@app.route('/health')
def health():
    """健康检查"""
    return jsonify({'status': 'healthy', 'nodes': len(service.nodes)})

if __name__ == '__main__':
    if service.load_config():
        if service.start_clash():
            print("节点轮换代理服务就绪")
            print(f"总节点数: {len(service.nodes)}")
    
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)
