import socket
import random
import time

class RenoServer:
    def __init__(self, port=2001, loss_rate=0.1):
        """
        Initialize the server with configurable packet loss rate
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('localhost', port))
        self.loss_rate = loss_rate
        self.last_acknowledged = -1  # Track the last in-order packet we acknowledged
        print(f"Server started on port {port} with {loss_rate*100}% packet loss")

    def run(self):
        """
        Listen for incoming packets and respond with ACKs
        Implements cumulative acknowledgments and tracks out-of-order packets
        """
        # Buffer for out-of-order packets
        received_buffer = set()
        
        while True:
            try:
                data, address = self.sock.recvfrom(1024)
                seq_num = int(data.decode())

                # Simulate packet loss
                if random.random() < self.loss_rate:
                    print(f"Simulating loss for packet {seq_num}")
                    continue
                    
                # Always acknowledge duplicates of already acknowledged packets
                if seq_num <= self.last_acknowledged:
                    ack_message = f"ACK:{self.last_acknowledged}"
                    print(f"Received duplicate packet {seq_num}, sending {ack_message}")
                    self.sock.sendto(ack_message.encode(), address)
                    continue

                if seq_num == self.last_acknowledged + 1:
                    # Correct next packet
                    self.last_acknowledged = seq_num
                    # Check if we can acknowledge more packets from the buffer
                    while self.last_acknowledged + 1 in received_buffer:
                        received_buffer.remove(self.last_acknowledged + 1)
                        self.last_acknowledged += 1
                    
                    ack_message = f"ACK:{self.last_acknowledged}"
                    print(f"Acknowledged up to packet {self.last_acknowledged}")
                else:
                    # Out-of-order packet: store it and resend last ACK
                    received_buffer.add(seq_num)
                    ack_message = f"ACK:{self.last_acknowledged}"
                    print(f"Received out-of-order packet {seq_num}, buffered, re-sending {ack_message}")

                self.sock.sendto(ack_message.encode(), address)
                
            except Exception as e:
                print(f"Error: {e}")
                break

if __name__ == "__main__":
    # Default port is 2001 to avoid conflict with Tahoe (2000)
    server = RenoServer(loss_rate=0.1)
    server.run()