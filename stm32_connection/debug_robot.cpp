#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <boost/bind/bind.hpp>
#include <iostream>
#include <string>
#include <vector>
#include <deque>
#include <memory>
#include <sstream>
#include <mutex>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>

namespace beast = boost::beast;
namespace websocket = beast::websocket;
namespace asio = boost::asio;
using tcp = boost::asio::ip::tcp;
using json = nlohmann::json;

// --- CONFIGURATION ---
const std::string SERIAL_PORT = "/dev/stm32"; 
const unsigned int BAUD_RATE = 115200;

// --- PORTS ---
const int WS_PORT_TELEMETRY = 8080;
const int WS_PORT_STATUS    = 8081; 
const int WS_PORT_GPS       = 8082; 
const int WS_PORT_CONTROL   = 8766; 
const int WS_PORT_LATENCY   = 9090; 

// --- UTILS ---
long current_time_ms() {
    auto now = std::chrono::system_clock::now();
    return std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
}

std::string clean_string(std::string s) {
    s.erase(remove(s.begin(), s.end(), '{'), s.end());
    s.erase(remove(s.begin(), s.end(), '}'), s.end());
    s.erase(remove(s.begin(), s.end(), '\n'), s.end());
    s.erase(remove(s.begin(), s.end(), '\r'), s.end());
    return s;
}

// --- 1. ROBUST WEBSOCKET SESSION (Output Queue) ---
class WSSession : public std::enable_shared_from_this<WSSession> {
    websocket::stream<tcp::socket> ws_;
    beast::flat_buffer buffer_;
    std::function<void(std::string)> on_message_;
    std::string role_; 
    
    // Outgoing Queue
    std::deque<std::string> queue_;
    bool alive_ = true;

public:
    WSSession(tcp::socket socket, std::function<void(std::string)> on_msg, std::string role)
        : ws_(std::move(socket)), on_message_(on_msg), role_(role) {}

    void set_callback(std::function<void(std::string)> cb) {
        on_message_ = cb;
    }

    bool is_alive() const { return alive_ && ws_.is_open(); }

    void run() {
        ws_.set_option(websocket::stream_base::timeout::suggested(beast::role_type::server));
        ws_.async_accept(beast::bind_front_handler(&WSSession::on_accept, shared_from_this()));
    }

    void on_accept(beast::error_code ec) {
        if (ec) { alive_ = false; return; }
        // Logging reduced: Only log if it's NOT latency
        if(role_ != "LATENCY") {
             // Optional: Uncomment to see connections once
             // std::cout << "[" << role_ << "] Connected." << std::endl; 
        }
        do_read();
    }

    void do_read() {
        ws_.async_read(buffer_, beast::bind_front_handler(&WSSession::on_read, shared_from_this()));
    }

    void on_read(beast::error_code ec, std::size_t bytes_transferred) {
        if (ec) { alive_ = false; return; }

        std::string msg = beast::buffers_to_string(buffer_.data());
        
        // --- DEBUG PRINT START ---
        // We filter out LATENCY pings to avoid spamming the console 100x a second
        if (role_ != "LATENCY") {
             std::cout << "[RX " << role_ << "]: " << msg << std::endl;
        }
        // --- DEBUG PRINT END ---

        buffer_.consume(buffer_.size()); 
        if (on_message_) on_message_(msg);
        do_read();
    }

    void send(std::string msg) {
        if (!alive_) return;
        asio::post(ws_.get_executor(), beast::bind_front_handler(&WSSession::on_send, shared_from_this(), msg));
    }

    void on_send(std::string msg) {
        if (!alive_) return;
        queue_.push_back(msg);
        if(queue_.size() > 1) return; // Already writing
        do_write();
    }

    void do_write() {
        if (!alive_) return;
        ws_.async_write(asio::buffer(queue_.front()), 
            beast::bind_front_handler(&WSSession::on_write, shared_from_this()));
    }

    void on_write(beast::error_code ec, std::size_t bytes_transferred) {
        if(ec) { alive_ = false; queue_.clear(); return; }
        queue_.pop_front();
        if(!queue_.empty()) do_write();
    }
};

// --- 2. MAIN BRIDGE CLASS (Serial Queue) ---
class Bridge {
    asio::io_context& ioc_;
    asio::serial_port serial_;
    
    // Serial Reading
    char rx_raw_buffer_[1024]; 
    std::string rx_data_stream_; 
    
    // Serial Writing Queue (CRITICAL FIX)
    std::deque<std::string> serial_write_queue_;
    
    tcp::acceptor acceptor_telem_;
    tcp::acceptor acceptor_status_;
    tcp::acceptor acceptor_gps_;
    tcp::acceptor acceptor_ctrl_;
    tcp::acceptor acceptor_latency_; 
    
    std::vector<std::shared_ptr<WSSession>> telem_clients_;
    std::vector<std::shared_ptr<WSSession>> status_clients_;
    std::vector<std::shared_ptr<WSSession>> gps_clients_;
    std::vector<std::shared_ptr<WSSession>> ctrl_clients_;
    
    std::mutex clients_mutex_;
    asio::steady_timer safety_timer_;
    asio::steady_timer gps_timer_; 
    
    long last_control_time = 0;

public:
    Bridge(asio::io_context& ioc) 
        : ioc_(ioc), 
          serial_(ioc),
          acceptor_telem_(ioc, tcp::endpoint(tcp::v4(), WS_PORT_TELEMETRY)),
          acceptor_status_(ioc, tcp::endpoint(tcp::v4(), WS_PORT_STATUS)),
          acceptor_gps_(ioc, tcp::endpoint(tcp::v4(), WS_PORT_GPS)),
          acceptor_ctrl_(ioc, tcp::endpoint(tcp::v4(), WS_PORT_CONTROL)),
          acceptor_latency_(ioc, tcp::endpoint(tcp::v4(), WS_PORT_LATENCY)),
          safety_timer_(ioc),
          gps_timer_(ioc)
    {
        connect_serial();
        do_accept_telem();
        do_accept_status();
        do_accept_gps();  
        do_accept_ctrl();
        do_accept_latency(); 
        run_safety_loop(); 
        run_gps_loop();    
    }

    void connect_serial() {
        try {
            if(serial_.is_open()) serial_.close();
            serial_.open(SERIAL_PORT);
            serial_.set_option(asio::serial_port_base::baud_rate(BAUD_RATE));
            serial_.set_option(asio::serial_port_base::character_size(8));
            serial_.set_option(asio::serial_port_base::parity(asio::serial_port_base::parity::none));
            serial_.set_option(asio::serial_port_base::stop_bits(asio::serial_port_base::stop_bits::one));
            
            std::cout << "[Serial] CONNECTED: " << SERIAL_PORT << " @ " << BAUD_RATE << std::endl;
            do_read_serial(); 
        } catch (std::exception& e) {
            std::cerr << "[Serial] ERROR: " << e.what() << ". Retrying..." << std::endl;
            auto timer = std::make_shared<asio::steady_timer>(ioc_, asio::chrono::seconds(2));
            timer->async_wait([this, timer](const boost::system::error_code&){ connect_serial(); });
        }
    }

    void do_read_serial() {
        serial_.async_read_some(asio::buffer(rx_raw_buffer_, sizeof(rx_raw_buffer_)), 
            [this](boost::system::error_code ec, std::size_t length) {
                if (!ec) {
                    rx_data_stream_.append(rx_raw_buffer_, length);

                    // Handshake
                    size_t handshake_pos = rx_data_stream_.find("CMD_HELLO");
                    if (handshake_pos != std::string::npos) {
                        // std::cout << "[Handshake] Sending ACK." << std::endl;
                        write_serial("ACK_READY"); 
                        rx_data_stream_.erase(handshake_pos, 9); 
                    }

                    // Lines
                    size_t newline_pos;
                    while ((newline_pos = rx_data_stream_.find('\n')) != std::string::npos) {
                        std::string line = rx_data_stream_.substr(0, newline_pos);
                        parse_stm32_line(line);
                        rx_data_stream_.erase(0, newline_pos + 1);
                    }
                    do_read_serial();  
                } else {
                    connect_serial(); 
                }
            });
    }

    // --- 3. SERIAL WRITE QUEUE (Prevents Control Crashes) ---
    void write_serial(std::string msg) {
        // Post to main thread context to manage queue safely
        asio::post(ioc_, [this, msg]() {
            bool write_in_progress = !serial_write_queue_.empty();
            serial_write_queue_.push_back(msg);
            
            if (!write_in_progress) {
                do_write_serial();
            }
        });
    }

    void do_write_serial() {
        if (!serial_.is_open()) {
            serial_write_queue_.clear();
            return;
        }

        asio::async_write(serial_, asio::buffer(serial_write_queue_.front()), 
            [this](boost::system::error_code ec, std::size_t) {
                if (!ec) {
                    serial_write_queue_.pop_front();
                    if (!serial_write_queue_.empty()) {
                        do_write_serial();
                    }
                } else {
                    // On error, clear queue and maybe reconnect
                    serial_write_queue_.clear();
                }
            });
    }

    void parse_stm32_line(std::string line) {
        if (line.find("CMD_HELLO") != std::string::npos) return; 

        if (line.find("\"type\":\"STATUS\"") != std::string::npos) {
            broadcast(status_clients_, line);
        } 
        else if (line.find("mode:") != std::string::npos || line.find("x:") != std::string::npos) {
            json j;
            j["type"] = "telemetry";
            j["timestamp"] = current_time_ms();
            std::string content = clean_string(line);
            std::stringstream ss(content);
            std::string segment;
            while(std::getline(ss, segment, ',')) {
                size_t delim = segment.find(':');
                if (delim != std::string::npos) {
                    std::string key = segment.substr(0, delim);
                    std::string val = segment.substr(delim + 1);
                    key.erase(0, key.find_first_not_of(" \t"));
                    try {
                        if (val.find('.') != std::string::npos) j[key] = std::stod(val);
                        else if (std::all_of(val.begin(), val.end(), ::isdigit) || val[0]=='-') j[key] = std::stoi(val);
                        else j[key] = val;
                    } catch (...) { j[key] = val; }
                }
            }
            broadcast(telem_clients_, j.dump());
        }
    }

    void broadcast(std::vector<std::shared_ptr<WSSession>>& clients, std::string payload) {
        std::lock_guard<std::mutex> lock(clients_mutex_);
        if (clients.empty()) return;

        clients.erase(
            std::remove_if(clients.begin(), clients.end(), 
            [&](std::shared_ptr<WSSession> s) { 
                if (!s->is_alive()) return true; 
                s->send(payload); 
                return false; 
            }), 
            clients.end()
        );
    }

    // --- ACCEPTORS (Standard) ---
    void do_accept_telem() {
        acceptor_telem_.async_accept([this](beast::error_code ec, tcp::socket socket) {
            if (!ec) {
                auto s = std::make_shared<WSSession>(std::move(socket), nullptr, "TELEMETRY");
                std::lock_guard<std::mutex> lock(clients_mutex_);
                telem_clients_.push_back(s);
                s->run();
            }
            do_accept_telem();
        });
    }
    void do_accept_status() {
        acceptor_status_.async_accept([this](beast::error_code ec, tcp::socket socket) {
            if (!ec) {
                auto s = std::make_shared<WSSession>(std::move(socket), nullptr, "STATUS");
                std::lock_guard<std::mutex> lock(clients_mutex_);
                status_clients_.push_back(s);
                s->run();
            }
            do_accept_status();
        });
    }
    void do_accept_gps() {
        acceptor_gps_.async_accept([this](beast::error_code ec, tcp::socket socket) {
            if (!ec) {
                auto s = std::make_shared<WSSession>(std::move(socket), nullptr, "GPS");
                std::lock_guard<std::mutex> lock(clients_mutex_);
                gps_clients_.push_back(s);
                s->run();
            }
            do_accept_gps();
        });
    }
    void do_accept_ctrl() {
        acceptor_ctrl_.async_accept([this](beast::error_code ec, tcp::socket socket) {
            if (!ec) {
                auto s = std::make_shared<WSSession>(std::move(socket), 
                    [this](std::string msg) { this->on_control_msg(msg); }, "CONTROL");
                std::lock_guard<std::mutex> lock(clients_mutex_);
                ctrl_clients_.push_back(s);
                s->run();
            }
            do_accept_ctrl();
        });
    }
    void do_accept_latency() {
        acceptor_latency_.async_accept([this](beast::error_code ec, tcp::socket socket) {
            if (!ec) {
                auto s = std::make_shared<WSSession>(std::move(socket), nullptr, "LATENCY");
                std::weak_ptr<WSSession> weak_s = s;
                s->set_callback([weak_s](std::string msg) {
                    if(auto strong_s = weak_s.lock()) {
                         size_t p = msg.find("ping");
                         if(p != std::string::npos) {
                             msg.replace(p, 4, "pong");
                             strong_s->send(msg);
                         }
                    }
                });
                s->run();
            }
            do_accept_latency();
        });
    }

    void run_gps_loop() {
        gps_timer_.expires_after(std::chrono::seconds(1));
        gps_timer_.async_wait([this](beast::error_code ec) {
            if (!ec) {
                double t_sec = current_time_ms() / 1000.0;
                json gps;
                gps["type"] = "gps";
                gps["timestamp"] = current_time_ms();
                gps["lat"] = 39.9334 + (0.0001 * std::sin(t_sec / 10.0));
                gps["lon"] = 32.8597 + (0.0001 * std::cos(t_sec / 10.0));
                gps["alt"] = 900;
                gps["status"] = 0;
                gps["service"] = 1;
                gps["position_covariance"] = std::vector<double>(9, 0.0); 
                gps["position_covariance_type"] = 0;
                broadcast(gps_clients_, gps.dump());
                run_gps_loop();
            }
        });
    }

    // --- 4. CONTROL HANDLER (Supports Mode & Velocity) ---
    void on_control_msg(std::string msg) {
        last_control_time = current_time_ms();
        try {
            auto j = json::parse(msg);
            
            // Handle MODE (e.g., {"mode": "ARMED"})
            if (j.contains("mode")) {
                std::string m = j["mode"];
                // Ensure uppercase if needed, or send raw
                // We append \n explicitly
                write_serial(m); 
            }

            // Handle Velocity (e.g., {"v": 0.5, "w": 0.1})
            if (j.contains("v") && j.contains("w")) {
                char cmd[64];
                snprintf(cmd, sizeof(cmd), "{v=%.2f, w=%.2f}", (double)j["v"], (double)j["w"]);
                write_serial(cmd);
            }
        } catch(...) {}
    }

    void run_safety_loop() {
        safety_timer_.expires_after(std::chrono::milliseconds(100));
        safety_timer_.async_wait([this](beast::error_code ec) {
            if (!ec && (current_time_ms() - last_control_time > 500)) {
                // Safety Timeout
                write_serial("{v=0.00, w=0.00}");
                write_serial("IDLE");
            }
            run_safety_loop();
        });
    }
};

int main() {
    try {
        asio::io_context ioc;
        Bridge bridge(ioc);
        std::cout << "--- ROBOT BRIDGE (DOUBLE QUEUE STABLE) STARTED ---" << std::endl;
        std::cout << "Ports: 8080(Tel), 8081(Sts), 8082(GPS), 8766(Ctrl), 9090(Lat)" << std::endl;
        ioc.run();
    } catch (std::exception& e) {
        std::cerr << "FATAL: " << e.what() << std::endl;
    }
    return 0;
}
