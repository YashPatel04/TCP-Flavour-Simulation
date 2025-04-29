import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import socket
import time
import sys
import os
import subprocess
import signal
import platform

# Add parent directory to path to import the client classes
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tahoe.tahoe_client import TahoeClient

# TCP Reno client implementation
class RenoClient(TahoeClient):
    def __init__(self):
        super().__init__()
        self.duplicate_ack_count = 0
        self.last_ack = -1
        self.in_fast_recovery = False
        # Use port 2001 for Reno instead of 2000 for Tahoe
        self.server_address = ('localhost', 2001)
        
    def run(self):
        try:
            total_packets = 100  # Default value, can be changed
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
                    time.sleep(0.005)  # 5ms delay between sends
                
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
                            # Set ssthresh to half of cwnd
                            self.ssthresh = max(self.cwnd / 2, 2)
                            
                            # Enter Fast Recovery
                            self.in_fast_recovery = True
                            self.cwnd = self.ssthresh + 3  # Inflated by 3 for the 3 duplicate ACKs
                            
                            # Retransmit the missing segment
                            next_to_send = ack_seq + 1
                            print(f"FAST RETRANSMIT: Resending packet {next_to_send}, ssthresh={self.ssthresh:.2f}, cwnd={self.cwnd:.2f}")
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
                    self.in_fast_recovery = False
                    self.duplicate_ack_count = 0
                    
                    # Retransmit the oldest unacknowledged packet
                    if self.in_flight:
                        next_to_send = min(self.in_flight)
                        print(f"TIMEOUT: Retransmitting {next_to_send}, new ssthresh={self.ssthresh:.2f}, cwnd={self.cwnd}")
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

# Main GUI class
class TCPCongestionGui:
    def __init__(self, root):
        # Basic setup
        self.root = root
        self.root.title("TCP Congestion Control Visualization")
        self.root.geometry("950x800")  # Slightly taller to accommodate server controls
        
        # Store server processes
        self.tahoe_server_process = None
        self.reno_server_process = None
        
        # Create tab control
        self.tab_control = ttk.Notebook(root)
        self.tahoe_tab = ttk.Frame(self.tab_control)
        self.reno_tab = ttk.Frame(self.tab_control)
        
        # Add tabs to notebook
        self.tab_control.add(self.tahoe_tab, text='TCP Tahoe')
        self.tab_control.add(self.reno_tab, text='TCP Reno')
        self.tab_control.pack(expand=1, fill="both")
        
        # Initialize data structures for each protocol
        self.init_data_structures()
        
        # Create tab contents
        self.initialize_tahoe_tab()
        self.initialize_reno_tab()
        
        # Handle window closing
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def init_data_structures(self):
        # Tahoe data
        self.tahoe_round_numbers = []
        self.tahoe_cwnd_values = []
        self.tahoe_ssthresh_values = []
        self.tahoe_packets_sent = []
        self.tahoe_events = []
        self.tahoe_event_rounds = []
        self.tahoe_simulation_running = False
        
        # New RTT tracking data structures
        self.tahoe_rtt_values = []
        self.tahoe_rtt_cwnd_values = []
        
        # Reno data
        self.reno_round_numbers = []
        self.reno_cwnd_values = []
        self.reno_ssthresh_values = []
        self.reno_packets_sent = []
        self.reno_events = []
        self.reno_event_rounds = []
        self.reno_simulation_running = False
        
        # New RTT tracking for Reno
        self.reno_rtt_values = []
        self.reno_rtt_cwnd_values = []
    
    def initialize_tahoe_tab(self):
        # Add server control section first
        self.create_server_controls(self.tahoe_tab, "Tahoe")
        # Then add simulation controls
        self.create_control_panel(self.tahoe_tab, "Tahoe")
        # Finally add the graph
        self.create_graph(self.tahoe_tab, "Tahoe")
        
    def initialize_reno_tab(self):
        # Add server control section first
        self.create_server_controls(self.reno_tab, "Reno")
        # Then add simulation controls
        self.create_control_panel(self.reno_tab, "Reno")
        # Finally add the graph
        self.create_graph(self.reno_tab, "Reno")
    
    def create_server_controls(self, parent, protocol):
        server_frame = ttk.LabelFrame(parent, text=f"{protocol} Server Controls")
        server_frame.pack(fill="x", padx=10, pady=5)
        
        # Server status indicator
        status_frame = ttk.Frame(server_frame)
        status_frame.pack(side=tk.TOP, fill="x", padx=5, pady=5)
        
        ttk.Label(status_frame, text="Server Status:").pack(side=tk.LEFT, padx=5)
        
        if protocol == "Tahoe":
            self.tahoe_server_status = tk.StringVar(value="Not Running")
            status_label = ttk.Label(status_frame, textvariable=self.tahoe_server_status)
            status_label.pack(side=tk.LEFT, padx=5)
            
            # Status indicator (colored circle)
            self.tahoe_status_indicator = tk.Canvas(status_frame, width=15, height=15, bg='red')
            self.tahoe_status_indicator.create_oval(2, 2, 13, 13, fill='red', outline='black')
            self.tahoe_status_indicator.pack(side=tk.LEFT, padx=5)
        else:
            self.reno_server_status = tk.StringVar(value="Not Running")
            status_label = ttk.Label(status_frame, textvariable=self.reno_server_status)
            status_label.pack(side=tk.LEFT, padx=5)
            
            # Status indicator (colored circle)
            self.reno_status_indicator = tk.Canvas(status_frame, width=15, height=15, bg=self.root['bg'])
            self.reno_status_indicator.create_oval(2, 2, 13, 13, fill='red', outline='black')
            self.reno_status_indicator.pack(side=tk.LEFT, padx=5)
        
        # Server configuration controls
        config_frame = ttk.Frame(server_frame)
        config_frame.pack(side=tk.TOP, fill="x", padx=5, pady=5)
        
        ttk.Label(config_frame, text="Loss Rate:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        if protocol == "Tahoe":
            self.tahoe_server_loss = tk.DoubleVar(value=0.1)
            ttk.Spinbox(config_frame, from_=0.0, to=0.9, increment=0.05, format="%.2f", textvariable=self.tahoe_server_loss, width=10).grid(
                row=0, column=1, padx=5, pady=5, sticky="w")
        else:
            self.reno_server_loss = tk.DoubleVar(value=0.1)
            ttk.Spinbox(config_frame, from_=0.0, to=0.9, increment=0.05, format="%.2f", textvariable=self.reno_server_loss, width=10).grid(
                row=0, column=1, padx=5, pady=5, sticky="w")
        
        # Start/Stop server buttons
        button_frame = ttk.Frame(server_frame)
        button_frame.pack(side=tk.TOP, fill="x", padx=5, pady=5)
        
        if protocol == "Tahoe":
            self.tahoe_start_server_btn = ttk.Button(button_frame, text="Start Server", 
                                                   command=lambda: self.start_server("Tahoe"))
            self.tahoe_start_server_btn.pack(side=tk.LEFT, padx=5)
            
            self.tahoe_stop_server_btn = ttk.Button(button_frame, text="Stop Server", 
                                                  command=lambda: self.stop_server("Tahoe"), state=tk.DISABLED)
            self.tahoe_stop_server_btn.pack(side=tk.LEFT, padx=5)
        else:
            self.reno_start_server_btn = ttk.Button(button_frame, text="Start Server", 
                                                  command=lambda: self.start_server("Reno"))
            self.reno_start_server_btn.pack(side=tk.LEFT, padx=5)
            
            self.reno_stop_server_btn = ttk.Button(button_frame, text="Stop Server", 
                                                 command=lambda: self.stop_server("Reno"), state=tk.DISABLED)
            self.reno_stop_server_btn.pack(side=tk.LEFT, padx=5)
    
    def create_control_panel(self, parent, protocol):
        control_frame = ttk.LabelFrame(parent, text=f"{protocol} Simulation Controls")
        control_frame.pack(fill="x", padx=10, pady=5)
        
        # First row of controls
        row1_frame = ttk.Frame(control_frame)
        row1_frame.pack(fill="x", padx=5, pady=5)
        
        # Packet count control
        ttk.Label(row1_frame, text="Total Packets:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        if protocol == "Tahoe":
            self.tahoe_packet_count = tk.IntVar(value=100)
            ttk.Spinbox(row1_frame, from_=10, to=1000, textvariable=self.tahoe_packet_count, width=10).grid(
                row=0, column=1, padx=5, pady=5, sticky="w")
        else:
            self.reno_packet_count = tk.IntVar(value=100)
            ttk.Spinbox(row1_frame, from_=10, to=1000, textvariable=self.reno_packet_count, width=10).grid(
                row=0, column=1, padx=5, pady=5, sticky="w")
        
        # Initial ssthresh control
        ttk.Label(row1_frame, text="Initial ssthresh:").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        if protocol == "Tahoe":
            self.tahoe_ssthresh = tk.IntVar(value=16)
            ttk.Spinbox(row1_frame, from_=2, to=100, textvariable=self.tahoe_ssthresh, width=10).grid(
                row=0, column=3, padx=5, pady=5, sticky="w")
        else:
            self.reno_ssthresh = tk.IntVar(value=16)
            ttk.Spinbox(row1_frame, from_=2, to=100, textvariable=self.reno_ssthresh, width=10).grid(
                row=0, column=3, padx=5, pady=5, sticky="w")
        
        # Second row of controls
        row2_frame = ttk.Frame(control_frame)
        row2_frame.pack(fill="x", padx=5, pady=5)
        
        # Timeout value control
        ttk.Label(row2_frame, text="Timeout (sec):").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        if protocol == "Tahoe":
            self.tahoe_timeout = tk.DoubleVar(value=0.5)
            ttk.Spinbox(row2_frame, from_=0.1, to=2.0, increment=0.1, format="%.1f", textvariable=self.tahoe_timeout, width=10).grid(
                row=0, column=1, padx=5, pady=5, sticky="w")
        else:
            self.reno_timeout = tk.DoubleVar(value=0.5)
            ttk.Spinbox(row2_frame, from_=0.1, to=2.0, increment=0.1, format="%.1f", textvariable=self.reno_timeout, width=10).grid(
                row=0, column=1, padx=5, pady=5, sticky="w")
        
        # Buttons row
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill="x", padx=5, pady=10)
        
        # Start button
        if protocol == "Tahoe":
            self.tahoe_start_button = ttk.Button(button_frame, text="Start Simulation", 
                                                command=lambda: self.start_simulation("Tahoe"))
            self.tahoe_start_button.pack(side=tk.LEFT, padx=10)
        else:
            self.reno_start_button = ttk.Button(button_frame, text="Start Simulation", 
                                              command=lambda: self.start_simulation("Reno"))
            self.reno_start_button.pack(side=tk.LEFT, padx=10)
        
        # Reset button
        if protocol == "Tahoe":
            self.tahoe_reset_button = ttk.Button(button_frame, text="Reset Simulation", 
                                               command=lambda: self.reset_simulation("Tahoe"))
            self.tahoe_reset_button.pack(side=tk.LEFT, padx=10)
        else:
            self.reno_reset_button = ttk.Button(button_frame, text="Reset Simulation", 
                                              command=lambda: self.reset_simulation("Reno"))
            self.reno_reset_button.pack(side=tk.LEFT, padx=10)
        
        # Status indicator
        status_frame = ttk.Frame(control_frame)
        status_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(status_frame, text="Simulation Status:").pack(side=tk.LEFT, padx=5)
        if protocol == "Tahoe":
            self.tahoe_status_var = tk.StringVar(value="Ready")
            ttk.Label(status_frame, textvariable=self.tahoe_status_var).pack(side=tk.LEFT, padx=5)
        else:
            self.reno_status_var = tk.StringVar(value="Ready")
            ttk.Label(status_frame, textvariable=self.reno_status_var).pack(side=tk.LEFT, padx=5)
    
    def create_graph(self, parent, protocol):
        # Create main frame to hold both graphs
        graphs_container = ttk.Frame(parent)
        graphs_container.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Top graph - Congestion Window over time (existing graph)
        cwnd_frame = ttk.LabelFrame(graphs_container, text=f"TCP {protocol} Congestion Window")
        cwnd_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Bottom graph - RTT vs cwnd (new graph)
        rtt_frame = ttk.LabelFrame(graphs_container, text=f"TCP {protocol} RTT vs Congestion Window")
        rtt_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        if protocol == "Tahoe":
            # Set up the cwnd over time graph (existing)
            self.tahoe_figure, self.tahoe_ax = plt.subplots(figsize=(8, 3))
            self.tahoe_ax.set_xlabel('Rounds (Packet Sequences)')
            self.tahoe_ax.set_ylabel('Congestion Window Size (segments)')
            self.tahoe_ax.set_title(f'TCP {protocol} Congestion Control')
            self.tahoe_ax.grid(True, linestyle='--', alpha=0.7)
            
            # Create canvas for cwnd graph
            self.tahoe_canvas = FigureCanvasTkAgg(self.tahoe_figure, master=cwnd_frame)
            self.tahoe_canvas.draw()
            self.tahoe_canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
            
            # Set up the RTT vs cwnd graph (new)
            self.tahoe_rtt_figure, self.tahoe_rtt_ax = plt.subplots(figsize=(8, 3))
            self.tahoe_rtt_ax.set_xlabel('RTT (seconds)')
            self.tahoe_rtt_ax.set_ylabel('Congestion Window Size (segments)')
            self.tahoe_rtt_ax.set_title(f'TCP {protocol} RTT vs Congestion Window')
            self.tahoe_rtt_ax.grid(True, linestyle='--', alpha=0.7)
            
            # Create canvas for RTT vs cwnd graph
            self.tahoe_rtt_canvas = FigureCanvasTkAgg(self.tahoe_rtt_figure, master=rtt_frame)
            self.tahoe_rtt_canvas.draw()
            self.tahoe_rtt_canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
        else:
            # Do the same for Reno (similar code)
            self.reno_figure, self.reno_ax = plt.subplots(figsize=(8, 3))
            self.reno_ax.set_xlabel('Rounds (Packet Sequences)')
            self.reno_ax.set_ylabel('Congestion Window Size (segments)')
            self.reno_ax.set_title(f'TCP {protocol} Congestion Control')
            self.reno_ax.grid(True, linestyle='--', alpha=0.7)
            
            self.reno_canvas = FigureCanvasTkAgg(self.reno_figure, master=cwnd_frame)
            self.reno_canvas.draw()
            self.reno_canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
            
            # Set up the RTT vs cwnd graph for Reno
            self.reno_rtt_figure, self.reno_rtt_ax = plt.subplots(figsize=(8, 3))
            self.reno_rtt_ax.set_xlabel('RTT (seconds)')
            self.reno_rtt_ax.set_ylabel('Congestion Window Size (segments)')
            self.reno_rtt_ax.set_title(f'TCP {protocol} RTT vs Congestion Window')
            self.reno_rtt_ax.grid(True, linestyle='--', alpha=0.7)
            
            # Create canvas for RTT vs cwnd graph
            self.reno_rtt_canvas = FigureCanvasTkAgg(self.reno_rtt_figure, master=rtt_frame)
            self.reno_rtt_canvas.draw()
            self.reno_rtt_canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
        
        # Results text area (keep this part from your original code)
        results_frame = ttk.LabelFrame(parent, text="Results")
        results_frame.pack(fill="x", padx=10, pady=5)
        
        if protocol == "Tahoe":
            self.tahoe_results_text = tk.Text(results_frame, height=6, width=80)
            self.tahoe_results_text.pack(fill="both", padx=5, pady=5)
        else:
            self.reno_results_text = tk.Text(results_frame, height=6, width=80)
            self.reno_results_text.pack(fill="both", padx=5, pady=5)

    def start_server(self, protocol):
        """Start the server for the specified protocol"""
        try:
            # Path to the server script for this protocol
            if protocol == "Tahoe":
                server_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                             "tahoe", "server.py")
                loss_rate = self.tahoe_server_loss.get()
                server_path = [sys.executable, server_script]
                
                # Create server process with the specified loss rate
                self.tahoe_server_process = subprocess.Popen(
                    server_path,
                    env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path), 'TCP_LOSS_RATE': str(loss_rate)}
                )
                
                # Update UI
                self.tahoe_server_status.set("Running")
                self.tahoe_status_indicator.itemconfig(1, fill='green')
                self.tahoe_start_server_btn.config(state=tk.DISABLED)
                self.tahoe_stop_server_btn.config(state=tk.NORMAL)
                
            else:  # Reno
                server_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                             "reno", "server.py")
                loss_rate = self.reno_server_loss.get()
                server_path = [sys.executable, server_script]
                
                # Create server process with the specified loss rate
                self.reno_server_process = subprocess.Popen(
                    server_path,
                    env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path), 'TCP_LOSS_RATE': str(loss_rate)}
                )
                
                # Update UI
                self.reno_server_status.set("Running")
                self.reno_status_indicator.itemconfig(1, fill='green')
                self.reno_start_server_btn.config(state=tk.DISABLED)
                self.reno_stop_server_btn.config(state=tk.NORMAL)
            
            # Log server start
            print(f"{protocol} server started with loss rate: {loss_rate:.2f}")
            
        except Exception as e:
            messagebox.showerror("Server Error", f"Failed to start {protocol} server: {str(e)}")
            print(f"Error starting {protocol} server: {e}")

    def stop_server(self, protocol):
        """Stop the server for the specified protocol"""
        try:
            if protocol == "Tahoe" and self.tahoe_server_process:
                # Terminate the process
                if platform.system() == "Windows":
                    # Windows needs taskkill to ensure child processes are killed
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(self.tahoe_server_process.pid)])
                else:
                    # Unix-like OS can use terminate
                    self.tahoe_server_process.terminate()
                    self.tahoe_server_process.wait(timeout=5)  # Wait up to 5 seconds for termination
                
                # Update UI
                self.tahoe_server_status.set("Not Running")
                self.tahoe_status_indicator.itemconfig(1, fill='red')
                self.tahoe_start_server_btn.config(state=tk.NORMAL)
                self.tahoe_stop_server_btn.config(state=tk.DISABLED)
                self.tahoe_server_process = None
                
            elif protocol == "Reno" and self.reno_server_process:
                # Terminate the process
                if platform.system() == "Windows":
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(self.reno_server_process.pid)])
                else:
                    self.reno_server_process.terminate()
                    self.reno_server_process.wait(timeout=5)
                
                # Update UI
                self.reno_server_status.set("Not Running")
                self.reno_status_indicator.itemconfig(1, fill='red')
                self.reno_start_server_btn.config(state=tk.NORMAL)
                self.reno_stop_server_btn.config(state=tk.DISABLED)
                self.reno_server_process = None
            
            print(f"{protocol} server stopped")
            
        except Exception as e:
            messagebox.showerror("Server Error", f"Failed to stop {protocol} server: {str(e)}")
            print(f"Error stopping {protocol} server: {e}")
    
    def on_closing(self):
        """Handle window closing: stop any running servers and clean up"""
        try:
            # Stop any running servers
            if self.tahoe_server_process:
                self.stop_server("Tahoe")
            if self.reno_server_process:
                self.stop_server("Reno")
            
            # Destroy the window
            self.root.destroy()
            
        except Exception as e:
            print(f"Error during cleanup: {e}")
            self.root.destroy()
    
    def check_server_running(self, protocol):
        """Check if the server for the specified protocol is running"""
        if protocol == "Tahoe":
            if not self.tahoe_server_process:
                return False
            # Check if process is still running
            return self.tahoe_server_process.poll() is None
        else:  # Reno
            if not self.reno_server_process:
                return False
            # Check if process is still running
            return self.reno_server_process.poll() is None
    
    def reset_simulation(self, protocol):
        if protocol == "Tahoe":
            # Existing reset code
            self.tahoe_simulation_running = False
            self.tahoe_round_numbers = []
            self.tahoe_cwnd_values = []
            self.tahoe_ssthresh_values = []
            self.tahoe_packets_sent = []
            self.tahoe_events = []
            self.tahoe_event_rounds = []
            
            # Reset RTT data
            self.tahoe_rtt_values = []
            self.tahoe_rtt_cwnd_values = []
            
            # Reset UI
            self.tahoe_status_var.set("Ready")
            self.tahoe_start_button['state'] = 'normal'
            self.tahoe_results_text.delete(1.0, tk.END)
            
            # Reset plots
            self.tahoe_ax.clear()
            self.tahoe_ax.set_xlabel('Rounds (Packet Sequences)')
            self.tahoe_ax.set_ylabel('Congestion Window Size (segments)')
            self.tahoe_ax.set_title('TCP Tahoe Congestion Control')
            self.tahoe_ax.grid(True, linestyle='--', alpha=0.7)
            self.tahoe_canvas.draw()
            
            self.tahoe_rtt_ax.clear()
            self.tahoe_rtt_ax.set_xlabel('RTT (seconds)')
            self.tahoe_rtt_ax.set_ylabel('Congestion Window Size (segments)')
            self.tahoe_rtt_ax.set_title('TCP Tahoe RTT vs Congestion Window')
            self.tahoe_rtt_ax.grid(True, linestyle='--', alpha=0.7)
            self.tahoe_rtt_canvas.draw()
            
        else:
            self.reno_simulation_running = False
            self.reno_round_numbers = []
            self.reno_cwnd_values = []
            self.reno_ssthresh_values = []
            self.reno_packets_sent = []
            self.reno_events = []
            self.reno_event_rounds = []
            
            # Reset RTT data
            self.reno_rtt_values = []
            self.reno_rtt_cwnd_values = []
            
            # Reset UI
            self.reno_status_var.set("Ready")
            self.reno_start_button['state'] = 'normal'
            self.reno_results_text.delete(1.0, tk.END)
            
            # Reset plots
            self.reno_ax.clear()
            self.reno_ax.set_xlabel('Rounds (Packet Sequences)')
            self.reno_ax.set_ylabel('Congestion Window Size (segments)')
            self.reno_ax.set_title('TCP Reno Congestion Control')
            self.reno_ax.grid(True, linestyle='--', alpha=0.7)
            self.reno_canvas.draw()
            
            self.reno_rtt_ax.clear()
            self.reno_rtt_ax.set_xlabel('RTT (seconds)')
            self.reno_rtt_ax.set_ylabel('Congestion Window Size (segments)')
            self.reno_rtt_ax.set_title('TCP Reno RTT vs Congestion Window')
            self.reno_rtt_ax.grid(True, linestyle='--', alpha=0.7)
            self.reno_rtt_canvas.draw()
    
    def start_simulation(self, protocol):
        # First check if the server is running
        if not self.check_server_running(protocol):
            messagebox.showerror("Server Error", 
                               f"The {protocol} server is not running. Please start the server first.")
            return
            
        self.reset_simulation(protocol)
        if protocol == "Tahoe":
            self.tahoe_simulation_running = True
            self.tahoe_start_button['state'] = 'disabled'
            self.tahoe_status_var.set("Running simulation...")
            
            # Start the simulation in a separate thread
            threading.Thread(target=lambda: self.run_simulation("Tahoe"), daemon=True).start()
            
            # Start updating the graph periodically
            self.update_graph_periodically("Tahoe")
        else:
            self.reno_simulation_running = True
            self.reno_start_button['state'] = 'disabled'
            self.reno_status_var.set("Running simulation...")
            
            # Start the simulation in a separate thread
            threading.Thread(target=lambda: self.run_simulation("Reno"), daemon=True).start()
            
            # Start updating the graph periodically
            self.update_graph_periodically("Reno")
    
    def update_graph_periodically(self, protocol):
        if protocol == "Tahoe" and self.tahoe_simulation_running:
            self.update_graph("Tahoe")
            # Schedule next update in 100ms
            self.root.after(100, lambda: self.update_graph_periodically("Tahoe"))
        elif protocol == "Reno" and self.reno_simulation_running:
            self.update_graph("Reno")
            # Schedule next update in 100ms
            self.root.after(100, lambda: self.update_graph_periodically("Reno"))
    
    def update_graph(self, protocol):
        if protocol == "Tahoe" and len(self.tahoe_round_numbers) > 0:
            # Existing code for updating cwnd graph (keep this part)
            self.tahoe_ax.clear()
            
            # Plot congestion window, threshold, and packets sent
            self.tahoe_ax.plot(self.tahoe_round_numbers, self.tahoe_cwnd_values, 'ko-', label='cwnd')
            self.tahoe_ax.plot(self.tahoe_round_numbers, self.tahoe_ssthresh_values, 'g--', label='threshold')
            if self.tahoe_packets_sent:
                self.tahoe_ax.plot(self.tahoe_round_numbers, self.tahoe_packets_sent, 'ro-', label='packets sent')
            
            # Add phase labels and vertical lines at event points
            self.add_phase_labels("Tahoe")
            
            # Configure axes and legend
            self.configure_axes("Tahoe")
            
            # Draw the updated graph
            self.tahoe_canvas.draw()
            
            # NEW: Update RTT vs cwnd graph
            if len(self.tahoe_rtt_values) > 0:
                self.tahoe_rtt_ax.clear()
                
                # First, we need to sort the data points by RTT values to get a proper line
                # Create pairs of (rtt, cwnd) and sort them by rtt
                data_points = sorted(zip(self.tahoe_rtt_values, self.tahoe_rtt_cwnd_values))
                
                # Unzip the sorted pairs
                sorted_rtts, sorted_cwnds = zip(*data_points) if data_points else ([], [])
                
                # Plot as a line with markers at data points
                self.tahoe_rtt_ax.plot(sorted_rtts, sorted_cwnds, 
                                     'b-o', linewidth=2, markersize=5, 
                                     label='RTT vs cwnd')
                
                # Add trendline if we have enough data points
                if len(self.tahoe_rtt_values) > 3:
                    try:
                        import numpy as np
                        from scipy import stats
                        
                        # Calculate trend line
                        slope, intercept, r_value, p_value, std_err = stats.linregress(
                            self.tahoe_rtt_values, self.tahoe_rtt_cwnd_values)
                        x = np.array([min(self.tahoe_rtt_values), max(self.tahoe_rtt_values)])
                        y = slope * x + intercept
                        
                        # Plot trendline
                        self.tahoe_rtt_ax.plot(x, y, 'r--', 
                                            label=f'Trend: y={slope:.4f}x+{intercept:.4f}, R²={r_value**2:.4f}')
                        self.tahoe_rtt_ax.legend(loc='upper left')
                    except ImportError:
                        # If scipy not available, skip trendline
                        pass
                
                # Configure RTT graph axes
                self.tahoe_rtt_ax.set_xlabel('RTT (seconds)')
                self.tahoe_rtt_ax.set_ylabel('Congestion Window Size (segments)')
                self.tahoe_rtt_ax.set_title('TCP Tahoe RTT vs Congestion Window')
                self.tahoe_rtt_ax.grid(True, linestyle='--', alpha=0.7)
                
                # Set better axis limits
                if len(self.tahoe_rtt_values) > 1:
                    x_margin = max(0.5, (max(self.tahoe_rtt_values) - min(self.tahoe_rtt_values)) * 0.1)
                    y_margin = (max(self.tahoe_rtt_cwnd_values) - min(self.tahoe_rtt_cwnd_values)) * 0.1
                    self.tahoe_rtt_ax.set_xlim(
                        max(0, min(self.tahoe_rtt_values) - x_margin),
                        max(self.tahoe_rtt_values) + x_margin
                    )
                    self.tahoe_rtt_ax.set_ylim(
                        max(0, min(self.tahoe_rtt_cwnd_values) - y_margin),
                        max(self.tahoe_rtt_cwnd_values) + y_margin
                    )
                
                # Draw the RTT graph
                self.tahoe_rtt_canvas.draw()
                
        elif protocol == "Reno" and len(self.reno_round_numbers) > 0:
            # Similar code for Reno (existing + RTT graph)
            # [Include the equivalent Reno version of the above code]
            self.reno_ax.clear()
            
            # Plot congestion window, threshold, and packets sent
            self.reno_ax.plot(self.reno_round_numbers, self.reno_cwnd_values, 'ko-', label='cwnd')
            self.reno_ax.plot(self.reno_round_numbers, self.reno_ssthresh_values, 'g--', label='threshold')
            if self.reno_packets_sent:
                self.reno_ax.plot(self.reno_round_numbers, self.reno_packets_sent, 'ro-', label='packets sent')
            
            # Add phase labels and vertical lines at event points
            self.add_phase_labels("Reno")
            
            # Configure axes and legend
            self.configure_axes("Reno")
            
            # Draw the updated graph
            self.reno_canvas.draw()
            
            # NEW: Update RTT vs cwnd graph
            if len(self.reno_rtt_values) > 0:
                self.reno_rtt_ax.clear()
                self.reno_rtt_ax.scatter(self.reno_rtt_values, self.reno_rtt_cwnd_values, 
                                        c='blue', alpha=0.6, marker='o', edgecolors='k', s=40)
                
                # Add trendline if we have enough data points
                if len(self.reno_rtt_values) > 3:
                    try:
                        import numpy as np
                        from scipy import stats
                        
                        # Calculate trend line
                        slope, intercept, r_value, p_value, std_err = stats.linregress(
                            self.reno_rtt_values, self.reno_rtt_cwnd_values)
                        x = np.array([min(self.reno_rtt_values), max(self.reno_rtt_values)])
                        y = slope * x + intercept
                        
                        # Plot trendline
                        self.reno_rtt_ax.plot(x, y, 'r--', 
                                            label=f'Trend: y={slope:.4f}x+{intercept:.4f}, R²={r_value**2:.4f}')
                        self.reno_rtt_ax.legend(loc='upper left')
                    except ImportError:
                        # If scipy not available, skip trendline
                        pass
                
                # Configure RTT graph axes
                self.reno_rtt_ax.set_xlabel('RTT (seconds)')
                self.reno_rtt_ax.set_ylabel('Congestion Window Size (segments)')
                self.reno_rtt_ax.set_title('TCP Reno RTT vs Congestion Window')
                self.reno_rtt_ax.grid(True, linestyle='--', alpha=0.7)
                
                # Set better axis limits
                if len(self.reno_rtt_values) > 1:
                    x_margin = max(0.5, (max(self.reno_rtt_values) - min(self.reno_rtt_values)) * 0.1)
                    y_margin = (max(self.reno_rtt_cwnd_values) - min(self.reno_rtt_cwnd_values)) * 0.1
                    self.reno_rtt_ax.set_xlim(
                        max(0, min(self.reno_rtt_values) - x_margin),
                        max(self.reno_rtt_values) + x_margin
                    )
                    self.reno_rtt_ax.set_ylim(
                        max(0, min(self.reno_rtt_cwnd_values) - y_margin),
                        max(self.reno_rtt_cwnd_values) + y_margin
                    )
                
                # Draw the RTT graph
                self.reno_rtt_canvas.draw()
    
    def add_phase_labels(self, protocol):
        if protocol == "Tahoe":
            # Add event markers
            for i, event in enumerate(self.tahoe_events):
                round_num = self.tahoe_event_rounds[i]
                idx = self.tahoe_round_numbers.index(round_num) if round_num in self.tahoe_round_numbers else -1
                if idx > 0 and idx < len(self.tahoe_round_numbers) - 1:
                    y_pos = max(self.tahoe_cwnd_values[idx], self.tahoe_ssthresh_values[idx]) * 0.5
                    if "Loss" in event:
                        self.tahoe_ax.text(round_num, y_pos, "Slowstart", 
                                        fontsize=8, ha='center', va='bottom')
                    self.tahoe_ax.axvline(x=round_num, color='r', linestyle=':', alpha=0.5)
            
            # Label congestion avoidance phase
            if len(self.tahoe_round_numbers) > 3:
                mid_point = len(self.tahoe_round_numbers) // 2
                if self.tahoe_cwnd_values[mid_point] > self.tahoe_ssthresh_values[mid_point]:
                    self.tahoe_ax.text(self.tahoe_round_numbers[mid_point], 
                                    self.tahoe_ssthresh_values[mid_point] * 0.7,
                                    "Congestion\nAvoidance", 
                                    fontsize=8, ha='center', va='bottom')
        else:
            # Add event markers
            for i, event in enumerate(self.reno_events):
                round_num = self.reno_event_rounds[i]
                idx = self.reno_round_numbers.index(round_num) if round_num in self.reno_round_numbers else -1
                if idx > 0 and idx < len(self.reno_round_numbers) - 1:
                    y_pos = max(self.reno_cwnd_values[idx], self.reno_ssthresh_values[idx]) * 0.5
                    if "Fast" in event:
                        self.reno_ax.text(round_num, y_pos, "Fast\nRecovery", 
                                       fontsize=8, ha='center', va='bottom')
                    elif "Loss" in event:
                        self.reno_ax.text(round_num, y_pos, "Slowstart", 
                                       fontsize=8, ha='center', va='bottom')
                    self.reno_ax.axvline(x=round_num, color='r', linestyle=':', alpha=0.5)
            
            # Label congestion avoidance phase
            if len(self.reno_round_numbers) > 3:
                mid_point = len(self.reno_round_numbers) // 2
                if self.reno_cwnd_values[mid_point] > self.reno_ssthresh_values[mid_point]:
                    self.reno_ax.text(self.reno_round_numbers[mid_point], 
                                   self.reno_ssthresh_values[mid_point] * 0.7,
                                   "Congestion\nAvoidance", 
                                   fontsize=8, ha='center', va='bottom')
    
    def configure_axes(self, protocol):
        if protocol == "Tahoe":
            # Set labels and title
            self.tahoe_ax.set_xlabel('Rounds (Packet Sequences)')
            self.tahoe_ax.set_ylabel('Congestion Window Size (segments)')
            self.tahoe_ax.set_title('TCP Tahoe Congestion Window')
            self.tahoe_ax.legend(loc='upper right')
            self.tahoe_ax.grid(True, linestyle='--', alpha=0.7)
            
            # Set better axis limits
            if len(self.tahoe_round_numbers) > 1:
                max_round = max(self.tahoe_round_numbers)
                self.tahoe_ax.set_xlim(0, max(max_round + 5, 20))
                
                all_y_values = self.tahoe_cwnd_values + self.tahoe_ssthresh_values
                if self.tahoe_packets_sent:
                    all_y_values += self.tahoe_packets_sent
                max_y = max(all_y_values) if all_y_values else 10
                self.tahoe_ax.set_ylim(0, max_y * 1.3)  # Add 30% margin
            
            # Adjust tick marks for readability
            if max(self.tahoe_round_numbers) > 20:
                self.tahoe_ax.xaxis.set_major_locator(plt.MaxNLocator(10))
            else:
                self.tahoe_ax.xaxis.get_major_locator().set_params(integer=True)
        else:
            # Set labels and title
            self.reno_ax.set_xlabel('Rounds (Packet Sequences)')
            self.reno_ax.set_ylabel('Congestion Window Size (segments)')
            self.reno_ax.set_title('TCP Reno Congestion Window')
            self.reno_ax.legend(loc='upper right')
            self.reno_ax.grid(True, linestyle='--', alpha=0.7)
            
            # Set better axis limits
            if len(self.reno_round_numbers) > 1:
                max_round = max(self.reno_round_numbers)
                self.reno_ax.set_xlim(0, max(max_round + 5, 20))
                
                all_y_values = self.reno_cwnd_values + self.reno_ssthresh_values
                if self.reno_packets_sent:
                    all_y_values += self.reno_packets_sent
                max_y = max(all_y_values) if all_y_values else 10
                self.reno_ax.set_ylim(0, max_y * 1.3)  # Add 30% margin
            
            # Adjust tick marks for readability
            if max(self.reno_round_numbers) > 20:
                self.reno_ax.xaxis.set_major_locator(plt.MaxNLocator(10))
            else:
                self.reno_ax.xaxis.get_major_locator().set_params(integer=True)
    
    def update_gui(self, protocol):
        """Final update when simulation completes"""
        if protocol == "Tahoe":
            self.tahoe_simulation_running = False
            self.update_graph("Tahoe")
            self.tahoe_start_button['state'] = 'normal'
            self.tahoe_status_var.set("Simulation completed")
        else:
            self.reno_simulation_running = False
            self.update_graph("Reno")
            self.reno_start_button['state'] = 'normal'
            self.reno_status_var.set("Simulation completed")
    
    def run_simulation(self, protocol):
        """Runs the appropriate TCP protocol simulation"""
        class InstrumentedTahoeClient(TahoeClient):
            def __init__(self, gui, ssthresh=16.0, timeout=0.5, packet_count=100):
                super().__init__()
                self.gui = gui
                self.ssthresh = ssthresh
                self.sock.settimeout(timeout)
                self.current_round = 0
                self.prev_cwnd = None
                self.packets_in_round = 0
                self.total_packets = packet_count
                
                # Add RTT tracking
                self.packet_send_times = {}
                
            def send_packet(self, seq):
                # Record send time for RTT calculation
                self.packet_send_times[seq] = time.time()
                super().send_packet(seq)
                
                # Add a 5ms delay between sending packets
                time.sleep(0.005)
                
                # Track packets sent in this round
                self.packets_in_round += 1
                
                # Increment round when we send the first packet in a window
                if seq % max(int(self.cwnd), 1) == 0:
                    self.current_round += 1
                    
                # Record data for visualization
                self.gui.tahoe_round_numbers.append(self.current_round)
                self.gui.tahoe_cwnd_values.append(self.cwnd)
                self.gui.tahoe_ssthresh_values.append(self.ssthresh)
                self.gui.tahoe_packets_sent.append(self.packets_in_round)
                
                # Detect congestion events
                if self.prev_cwnd is not None and self.cwnd < self.prev_cwnd:
                    self.gui.tahoe_events.append("Loss Event")
                    self.gui.tahoe_event_rounds.append(self.current_round)
                    # Reset packets counter after loss
                    self.packets_in_round = 0
                
                self.prev_cwnd = self.cwnd
                
            def run(self):
                self.start = time.time()
                
                try:
                    while len(self.acknowledged) < self.total_packets:
                        # Send packets up to the current window size
                        while self.next_seq < self.total_packets and len(self.in_flight) < int(self.cwnd):
                            self.send_packet(self.next_seq)
                            self.next_seq += 1
                        
                        if not self.in_flight and self.next_seq >= self.total_packets:
                            break
                            
                        # Try to receive ACKs
                        try:
                            data, _ = self.sock.recvfrom(1024)
                            ack_str = data.decode()
                            ack_seq = int(ack_str.split(":")[1])
                            
                            # Calculate RTT for this packet
                            if ack_seq in self.packet_send_times:
                                rtt = time.time() - self.packet_send_times[ack_seq]
                                self.gui.tahoe_rtt_values.append(rtt)
                                self.gui.tahoe_rtt_cwnd_values.append(self.cwnd)
                                print(f"RTT for packet {ack_seq}: {rtt:.4f}s, cwnd={self.cwnd:.2f}")
                                # Clean up
                                del self.packet_send_times[ack_seq]
                            
                            # Handle the acknowledgment - Properly implement cumulative ACKs
                            acked_packets = 0
                            in_flight_copy = self.in_flight.copy()
                            for seq in in_flight_copy:
                                if seq <= ack_seq:  # Cumulative acknowledgment
                                    self.in_flight.remove(seq)
                                    self.acknowledged.add(seq)
                                    acked_packets += 1
                            
                            if acked_packets > 0:
                                print(f"Received ACK for {ack_seq}, window={self.cwnd:.2f}")
                                
                                # Update the congestion window based on the phase
                                if self.cwnd < self.ssthresh:
                                    # Slow start phase: exponential growth
                                    self.cwnd = self.cwnd * 2
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
                                if next_to_send < self.total_packets:
                                    self.send_packet(next_to_send)
                
                except Exception as e:
                    print(f"Error in Tahoe simulation: {e}")
                
                self.end = time.time()
                
                # Record final state
                self.gui.tahoe_round_numbers.append(self.current_round)
                self.gui.tahoe_cwnd_values.append(self.cwnd)
                self.gui.tahoe_ssthresh_values.append(self.ssthresh)
                self.gui.tahoe_packets_sent.append(self.packets_in_round)
                
                # Update the GUI from the main thread
                self.gui.root.after(100, lambda: self.gui.update_gui("Tahoe"))
                
            def report(self):
                super().report()
                report_text = (
                    f"TCP Tahoe Results:\n"
                    f"Sent: {self.sent_packets}, Received: {len(self.acknowledged)}, "
                    f"Retransmissions: {self.retransmissions}\n"
                    f"Final cwnd: {self.cwnd:.2f}, Final ssthresh: {self.ssthresh:.2f}\n"
                    f"Total rounds: {self.current_round}, Total time: {self.end - self.start:.2f} seconds"
                )
                
                # Update text result in GUI
                self.gui.root.after(0, lambda: self.gui.tahoe_results_text.insert(tk.END, report_text))
        
        class InstrumentedRenoClient(RenoClient):
            def __init__(self, gui, ssthresh=16.0, timeout=0.5, packet_count=100):
                super().__init__()
                self.gui = gui
                self.ssthresh = ssthresh
                self.sock.settimeout(timeout)
                self.current_round = 0
                self.prev_cwnd = None
                self.packets_in_round = 0
                self.total_packets = packet_count
                
            def send_packet(self, seq):
                super().send_packet(seq)
                
                # Add a 5ms delay between sending packets
                time.sleep(0.005)
                
                # Track packets sent in this round
                self.packets_in_round += 1
                
                # Increment round when we send the first packet in a window
                if seq % max(int(self.cwnd), 1) == 0:
                    self.current_round += 1
                    
                # Record data for visualization
                self.gui.reno_round_numbers.append(self.current_round)
                self.gui.reno_cwnd_values.append(self.cwnd)
                self.gui.reno_ssthresh_values.append(self.ssthresh)
                self.gui.reno_packets_sent.append(self.packets_in_round)
                
                # Detect congestion events
                if self.prev_cwnd is not None and self.cwnd < self.prev_cwnd:
                    if self.in_fast_recovery:
                        self.gui.reno_events.append("Fast Recovery Event")
                    else:
                        self.gui.reno_events.append("Loss Event")
                    self.gui.reno_event_rounds.append(self.current_round)
                    # Reset packets counter after loss
                    self.packets_in_round = 0
                
                self.prev_cwnd = self.cwnd
                
            def run(self):
                # Run the full simulation using super().run()
                # We don't need to override it since RenoClient already has
                # all the necessary functionality
                self.total_packets = self.total_packets  # Use correct packet count
                self.start = time.time()
                super().run()
                
                # Record final state before exiting
                self.gui.reno_round_numbers.append(self.current_round)
                self.gui.reno_cwnd_values.append(self.cwnd)
                self.gui.reno_ssthresh_values.append(self.ssthresh)
                self.gui.reno_packets_sent.append(self.packets_in_round)
                
                # Update the GUI from the main thread
                self.gui.root.after(100, lambda: self.gui.update_gui("Reno"))
                
            def report(self):
                super().report()
                fast_recovery_events = sum(1 for e in self.gui.reno_events if 'Fast' in e)
                report_text = (
                    f"TCP Reno Results:\n"
                    f"Sent: {self.sent_packets}, Received: {len(self.acknowledged)}, "
                    f"Retransmissions: {self.retransmissions}\n"
                    f"Final cwnd: {self.cwnd:.2f}, Final ssthresh: {self.ssthresh:.2f}\n"
                    f"Total rounds: {self.current_round}, Total time: {self.end - self.start:.2f} seconds\n"
                    f"Fast Recovery events: {fast_recovery_events}"
                )
                
                # Update text result in GUI
                self.gui.root.after(0, lambda: self.gui.reno_results_text.insert(tk.END, report_text))
        
        try:
            # Run the appropriate client based on protocol
            if protocol == "Tahoe":
                client = InstrumentedTahoeClient(
                    self,
                    ssthresh=float(self.tahoe_ssthresh.get()),
                    timeout=float(self.tahoe_timeout.get()),
                    packet_count=int(self.tahoe_packet_count.get())
                )
            else:
                client = InstrumentedRenoClient(
                    self,
                    ssthresh=float(self.reno_ssthresh.get()),
                    timeout=float(self.reno_timeout.get()),
                    packet_count=int(self.reno_packet_count.get())
                )
            client.run()
            client.report()
            
        except Exception as e:
            print(f"Error in {protocol} simulation: {e}")
            if protocol == "Tahoe":
                self.tahoe_status_var.set(f"Error: {e}")
                self.tahoe_simulation_running = False
                self.tahoe_start_button['state'] = 'normal'
            else:
                self.reno_status_var.set(f"Error: {e}")
                self.reno_simulation_running = False
                self.reno_start_button['state'] = 'normal'

if __name__ == "__main__":
    root = tk.Tk()
    app = TCPCongestionGui(root)
    root.mainloop()