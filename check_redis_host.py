import socket
import dns.resolver

def check_redis_host():
    host = 'fly-calendar-bot-redis.upstash.io'
    print(f"\nChecking DNS resolution for {host}...")
    
    try:
        ip_address = socket.gethostbyname(host)
        print(f"IP address: {ip_address}")
        
        answers = dns.resolver.resolve(host, 'A')
        print("\nAll A records:")
        for rdata in answers:
            print(f"- {rdata}")
            
    except socket.gaierror as e:
        print(f"DNS resolution error: {e}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_redis_host()
