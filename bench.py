import argparse
import requests
import time
import sys
import re
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

class HostTestReport:
    host: str = ''
    success: int = 0
    failed: int = 0
    errors: int = 0
    min: float = float('inf')
    max: float = -1
    avg: float = -1

    def __init__(self, host: str, response_list: list[requests.Response]):
        self.host = host

        elapsed_time_sum = 0
        for response in response_list:
            if response is None:
                self.errors += 1
                continue

            if response.status_code == 200: # OK
                self.success += 1
                elapsed_time_ms = response.elapsed.microseconds / 1000
                elapsed_time_sum += elapsed_time_ms
                if self.max < elapsed_time_ms:
                    self.max = elapsed_time_ms
                if self.min > elapsed_time_ms:
                    self.min = elapsed_time_ms
                
            elif 400 <= response.status_code < 600: #4xx & 5xx
                self.failed += 1

        self.avg = elapsed_time_sum / self.success if self.success else 0
    
    def to_string(self):
        return f'''
------------------------------------
host    = {self.host}
success = {self.success}
failed  = {self.failed}
errors  = {self.errors}
max     = {self.max:.1f} ms
min     = {self.min:.1f} ms
avg     = {self.avg:.1f} ms
'''


class HostHttpBenchmark:
    reports: dict[str, HostTestReport]

    def get_response_list(self, url: str, timeout: float, count: int = 1) -> list[Optional[requests.Response]]:
        if count <= 0:
            raise Exception('Кол-во запросов должно быть больше 0')
        
        return [self.get_response(url = url, timeout=timeout) for _ in range(count)]
        
    def get_response(self, url: str, timeout: float) -> Optional[requests.Response]:
        try:
            response = requests.get(url, timeout=timeout)
            return response
        except (requests.exceptions.RequestException, requests.exceptions.Timeout):
            return None

    def get_response_list_mock(self):
        return [
            self.get_response('https://httpbin.org/status/200', 5),
            self.get_response('https://httpbin.org/status/200', 5),
            self.get_response('https://httpbin.org/status/200', 5),
            self.get_response('https://httpbin.org/status/404', 5), #Failed
            self.get_response('https://httpbin.org/status/503', 5), #failed
            self.get_response('https://httpbin.org/delay/10', 5), # error
            self.get_response('https://httpbin.org/status/200', 5),
            self.get_response('https://www.youtube.com/', 10), # Error
            self.get_response('https://httpbin.org/status/200', 5),
            self.get_response('https://httpbin.org/status/200', 5)
        ]

class HttpHostTestService:
    benchmark: HostHttpBenchmark = HostHttpBenchmark()
    url_pattern: re.Pattern = re.compile(
            r'^(https?)://'         # http:// or https://
            r'([a-zA-Z0-9.-]+)'     # domain
            r'(\.[a-zA-Z]{2,})'     # .com, .org etc
            r'(/[^\s]*)?$'          # path
        )
    
    def validate_url(self, url: str) -> bool:
        return bool(self.url_pattern.match(url))
    
    def test_host(self, host: str, timeout: int = 5, count: int = 1):
        if self.validate_url(host):
            responses = self.benchmark.get_response_list(host, timeout, count)
            return HostTestReport(host, responses)
        else: 
            raise Exception(f"Ошибка в формате хоста: {host}")
        
    def test_hosts(self, hosts: list[str], timeout: int = 5, count: int = 1):
        reports: list[HostTestReport] = []
        try:
            for host in hosts:
                reports.append(self.test_host(host, timeout, count))
        except Exception as e:
            print(f'Ошибка при тестировании доступности хоста: {e}')

        return reports
    
    def test_hosts_parallel(self, hosts: list[str], timeout: int = 5, count: int = 1, thread_count: int = 1) -> list[HostTestReport]:
        reports: list[HostTestReport] = []
        
        # Распараллеливаем тестирование разных хостов
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {
                executor.submit(self.test_host, host, timeout, count): host 
                for host in hosts
            }
            
            for future in as_completed(futures):
                try:
                    reports.append(future.result())
                except Exception as e:
                    print(f"Ошибка при тестировании доступности хоста: {futures[future]}: {e}")

        return reports
    

def read_hosts_from_file(filepath: str) -> Optional[list[str]]:
    try:
        with open(filepath, "r", encoding="utf-8") as input_file:
            hosts = []
            for line in input_file:
                stripped = line.strip()
                if len(stripped) > 0:
                    hosts.append(stripped)
            return hosts
    except Exception as e:
        print(f"Ошибка чтения файла: {e}")
        return None
        
def write_reports(filepath: str, reports: list[HostTestReport]):
    try:
        with open(filepath, 'w+', encoding="utf-8") as output_file:
            for report in reports:
                output_file.write(report.to_string())
                   
    except Exception as e:
        print(f"Ошибка записи в файл: {e}")

def print_reports(reports: list[HostTestReport]):
    for report in reports:
        print(report.to_string())


def main():
    parser = argparse.ArgumentParser(description="HTTP Host Benchmark")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-H", "--hosts", type=str, help="Список хостов через запятую")
    group.add_argument("-F", "--file", type=str, help="Файл со списком хостов")
    parser.add_argument("-C", "--count", type=int, default=1, help="Количество запросов на хост (по умолчанию 1)")
    parser.add_argument("-O", "--output", type=str, help="Файл для вывода статистики")
    parser.add_argument("-P", "--parallel_threads", type=int, help="Кол-во потоков для параллельного выполнения запросов")
    args = parser.parse_args()

    if args.count < 1:
        print("Параметр --count должен быть положительным числом.")
        sys.exit(1)

    host_test_service = HttpHostTestService()

    if args.hosts:
        hosts = args.hosts.split(",")
    else:
        hosts = read_hosts_from_file(args.file)

    if len(hosts) == 0:
        print("Список хостов пуст.")
        sys.exit(1)

    print(hosts)

    if args.parallel_threads is not None :
        reports = host_test_service.test_hosts_parallel(hosts=hosts, count=args.count, thread_count=args.parallel_threads)
    else:
        reports = host_test_service.test_hosts(hosts=hosts, count=args.count)
  
    if args.output is not None:
        write_reports(args.output, reports)
    else:
        print_reports(reports)
        

if __name__ == '__main__':
    main()
