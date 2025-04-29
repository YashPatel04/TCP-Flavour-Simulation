import socket
import sys
import time
import random

client_timeout = 0.5  # timeout in seconds
total_packets = 100

class RenoClient:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(client_timeout)
        # Use port 2001 for Reno to avoid conflict with Tahoe (2000)
        self.server_address = ('localhost', 2001)
        self.cwnd = 1.0          # Start with window size of 1 segment
        self.ssthresh = 16.0     # Slow start threshold
        self.next_seq = 0        # Next sequence number to send
        self.acknowledged = set() # Set of acknowledged packets
        self.sent_packets = 0    # Total packets sent (including retransmissions)
        self.retransmissions = 0 # Count of retransmissions
        self.in_flight = set()   # Packets currently in flight
        self.sent_times = {}     # Track when each packet was sent
        
        # Reno-specific variables
        self.duplicate_ack_count = 0
        self.last_ack = -1
        self.in_fast_recovery = False
        
        print(f"TCP Reno client started, connecting to {self.server_address}")
        
    def send_packet(self, seq):
        try:
            self.sock.sendto(str(seq).encode(), self.server_address)
            self.sent_packets += 1
            self.in_flight.add(seq)
            self.sent_times[seq] = time.time()
            print(f"Sent packet {seq}, cwnd={self.cwnd:.2f}")
            
            # Add a small delay to prevent overwhelming the network
            time.sleep(0.005)   
        except Exception as e:
            print(f"Error sending packet {seq}: {e}")
        
    def run(self):
        try:
            self.start = time.time()
            
            # Add a maximum retransmission counter to prevent infinite loops
            max_retransmissions = 50
            retransmission_counter = 0
            
            while len(self.acknowledged) < total_packets:
                if retransmission_counter > max_retransmissions:
                    print(f"Exiting due to excessive retransmissions")
                    break
                    
                # Send packets up to the current window size
                while self.next_seq < total_packets and len(self.in_flight) < int(self.cwnd):
                    self.send_packet(self.next_seq)
                    self.next_seq += 1
                
                if not self.in_flight and self.next_seq >= total_packets:
                    break
                    
                # Try to receive ACKs
                try:
                    data, _ = self.sock.recvfrom(1024)
                    ack_str = data.decode()
                    ack_seq = int(ack_str.split(":")[1])
                    
                    # Check if this is a duplicate ACK
                    if ack_seq == self.last_ack:
                        self.duplicate_ack_count += 1
                        print(f"Duplicate ACK for {ack_seq} (count: {self.duplicate_ack_count})")
                        
                        # Fast Retransmit - After 3 duplicate ACKs
                        if self.duplicate_ack_count == 3:
                            # Set ssthresh to half of current cwnd
                            self.ssthresh = max(self.cwnd / 2, 2)
                            
                            # Enter Fast Recovery
                            self.in_fast_recovery = True
                            self.cwnd = self.ssthresh + 3  # Inflated by 3 for the 3 duplicate ACKs
                            
                            # Retransmit the missing segment
                            next_to_send = ack_seq + 1
                            print(f"3 DUP ACKs: Fast retransmit packet {next_to_send}, ssthresh={self.ssthresh:.2f}, cwnd={self.cwnd:.2f}")
                            self.send_packet(next_to_send)
                            self.retransmissions += 1
                            retransmission_counter += 1
                        
                        elif self.duplicate_ack_count > 3 and self.in_fast_recovery:
                            # For each additional duplicate ACK, inflate cwnd by 1
                            self.cwnd += 1
                            print(f"Fast Recovery - Inflating cwnd to {self.cwnd:.2f}")
                    else:
                        # New ACK received
                        self.last_ack = ack_seq
                        self.duplicate_ack_count = 0
                        
                        # Exit Fast Recovery if we were in it
                        if self.in_fast_recovery:
                            self.cwnd = self.ssthresh  # Deflate back to ssthresh
                            self.in_fast_recovery = False
                            print(f"Exiting Fast Recovery, cwnd={self.cwnd:.2f}")
                    
                    # Handle the acknowledgment - acknowledge all packets up to and including ack_seq
                    acked_packets = 0
                    in_flight_copy = self.in_flight.copy()  # Create a copy to safely iterate
                    for seq in in_flight_copy:
                        if seq <= ack_seq:  # Cumulative acknowledgment
                            self.in_flight.remove(seq)
                            self.acknowledged.add(seq)
                            acked_packets += 1
                    
                    if acked_packets > 0:
                        print(f"Acknowledged {acked_packets} packets, window={self.cwnd:.2f}")
                        
                        # Update the congestion window based on the phase (if not in fast recovery)
                        if not self.in_fast_recovery:
                            if self.cwnd < self.ssthresh:
                                # Slow start phase: exponential growth
                                self.cwnd = self.cwnd * 2
                            else:
                                # Congestion avoidance phase: additive increase
                                self.cwnd += 1/self.cwnd
                        
                except socket.timeout:
                    # For timeout, Reno behaves like Tahoe: reset to slow start
                    self.ssthresh = max(self.cwnd / 2, 2)
                    self.cwnd = 1
                    self.retransmissions += 1
                    retransmission_counter += 1
                    self.in_fast_recovery = False  # Exit fast recovery
                    self.duplicate_ack_count = 0   # Reset duplicate ACK count
                    
                    print("TIMEOUT: Performing slow start")
                    
                    # Retransmit the oldest unacknowledged packet
                    if self.in_flight:
                        next_to_send = min(self.in_flight)
                        print(f"Retransmitting packet {next_to_send}")
                        if next_to_send < total_packets:
                            self.send_packet(next_to_send)
                    else:
                        # Try to move forward if no packets in flight
                        if self.next_seq < total_packets:
                            print(f"Sending next packet {self.next_seq}")
                            self.send_packet(self.next_seq)
                            self.next_seq += 1
            
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
        goodput = len(self.acknowledged) / max(1, self.sent_packets)
        
        print(f"\nTCP Reno Results:")
        print(f"Sent: {self.sent_packets}, Received: {len(self.acknowledged)}, Retransmissions: {self.retransmissions}")
        print(f"Final cwnd: {self.cwnd:.2f}, Final ssthresh: {self.ssthresh:.2f}")
        print(f"Throughput: {throughput:.2f} packets/sec")
        print(f"Goodput: {goodput:.2f}")
        print(f"Total time: {duration:.2f} seconds")


if __name__ == "__main__":
    client = RenoClient()
    try:
        client.run()
    finally:
        client.report()