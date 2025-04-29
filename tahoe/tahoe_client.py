import socket
import sys
import time
import random

client_timeout = 0.5  # timeout in seconds
total_packets = 100

class TahoeClient:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(client_timeout)
        self.server_address = ('localhost', 8000)
        self.cwnd = 1.0          # Start with window size of 1 segment
        self.ssthresh = 16.0     # Slow start threshold
        self.next_seq = 0        # Next sequence number to send
        self.base = 0            # Oldest unacknowledged sequence number
        self.acknowledged = set() # Set of acknowledged packets
        self.sent_packets = 0    # Total packets sent (including retransmissions)
        self.retransmissions = 0 # Count of retransmissions
        self.dup_acks = {}       # Track duplicate ACKs
        self.in_flight = set()   # Packets currently in flight
        self.sent_times = {}     # Track when each packet was sent
        
    def send_packet(self, seq):
        try:
            self.sock.sendto(str(seq).encode(), self.server_address)
            self.sent_packets += 1
            self.in_flight.add(seq)
            self.sent_times[seq] = time.time()
            print(f"Sent packet {seq}, cwnd={self.cwnd:.2f}")
        except Exception as e:
            print(f"Error sending packet {seq}: {e}")
        
    def run(self):
        try:
            self.start = time.time()
            
            while len(self.acknowledged) < total_packets:
                # Send packets up to the current window size
                while self.next_seq < total_packets and len(self.in_flight) < int(self.cwnd):
                    self.send_packet(self.next_seq)
                    self.next_seq += 1
                
                if not self.in_flight and self.next_seq > total_packets:  # If no packets in flight, we're done
                    break
                    
                # Try to receive ACKs
                try:
                    data, _ = self.sock.recvfrom(1024)
                    ack_str = data.decode()
                    ack_seq = int(ack_str.split(":")[1])
                    
                    # Handle the acknowledgment
                    if ack_seq in self.in_flight:
                        self.in_flight.remove(ack_seq)
                        self.acknowledged.add(ack_seq)
                        print(f"Received ACK for {ack_seq}, window={self.cwnd:.2f}")
                        
                        # Update the congestion window based on the phase
                        if self.cwnd < self.ssthresh:
                            # Slow start phase: exponential growth
                            self.cwnd = self.cwnd*2
                        else:
                            # Congestion avoidance phase: additive increase
                            self.cwnd += 1/self.cwnd
                        
                except socket.timeout:
                    # Timeout => packet loss detected
                    # TCP Tahoe: reset to slow start
                    self.ssthresh = max(self.cwnd / 2, 2)
                    self.cwnd = 1
                    self.retransmissions += 1
                    
                    # Retransmit the oldest unacknowledged packet
                    if self.in_flight:
                        next_to_send = min(self.in_flight)
                        print(f"TIMEOUT: Retransmitting {next_to_send}, new ssthresh={self.ssthresh:.2f}, cwnd={self.cwnd}")
                        if next_to_send < total_packets:
                            self.send_packet(next_to_send)
            
            self.end = time.time()
            
        except KeyboardInterrupt:
            print("Client stopped by user")
            self.end = time.time()
        except Exception as e:
            print(f"Error in client: {e}")
            self.end = time.time()
        
    def report(self):
        duration = self.end - self.start
        throughput = len(self.acknowledged) / duration if duration > 0 else 0
        goodput = len(self.acknowledged) / max(1, self.sent_packets)  # Avoid division by zero
        
        print(f"\nTCP Tahoe Results:")
        print(f"Sent: {self.sent_packets}, Received: {len(self.acknowledged)}, Retransmissions: {self.retransmissions}")
        print(f"Final cwnd: {self.cwnd:.2f}, Final ssthresh: {self.ssthresh:.2f}")
        print(f"Throughput: {throughput:.2f} packets/sec")
        print(f"Goodput: {goodput:.2f}")
        print(f"Total time: {duration:.2f} seconds")


if __name__ == "__main__":
    client = TahoeClient()
    try:
        client.run()
    finally:
        client.report()