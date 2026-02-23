#include <chrono>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

static long long now_ms()
{
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static std::string timestamp()
{
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto ms = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
    const std::time_t tt = system_clock::to_time_t(now);

    std::tm local_tm{};
#if defined(_WIN32)
    localtime_s(&local_tm, &tt);
#else
    localtime_r(&tt, &local_tm);
#endif

    std::ostringstream oss;
    oss << std::put_time(&local_tm, "%Y-%m-%d %H:%M:%S") << '.' << std::setw(3) << std::setfill('0') << ms.count();
    return oss.str();
}

enum class Level
{
    Debug,
    Info,
    Warning,
    Error,
};

static const char* level_name(Level lvl)
{
    switch (lvl)
    {
    case Level::Debug:   return "DEBUG";
    case Level::Info:    return "INFO";
    case Level::Warning: return "WARNING";
    case Level::Error:   return "ERROR";
    }
    return "INFO";
}

static std::string join_segments(const std::vector<std::string>& segs)
{
    std::ostringstream oss;
    bool first = true;
    for (const auto& s : segs)
    {
        if (s.empty())
        {
            continue;
        }
        if (!first)
        {
            oss << " | ";
        }
        first = false;
        oss << s;
    }
    return oss.str();
}

static std::string build_kvs(const std::vector<std::pair<std::string, std::string>>& kvs)
{
    std::ostringstream oss;
    bool first = true;
    for (const auto& kv : kvs)
    {
        if (kv.first.empty() || kv.second.empty())
        {
            continue;
        }
        if (!first)
        {
            oss << ' ';
        }
        first = false;
        oss << kv.first << '=' << kv.second;
    }
    return oss.str();
}

static std::string build_message(const std::string& event,
                                 const std::string& action,
                                 const std::vector<std::pair<std::string, std::string>>& key,
                                 const std::string& result,
                                 const std::string& reason,
                                 const std::vector<std::pair<std::string, std::string>>& tail)
{
    std::vector<std::string> segs;

    if (!event.empty())
    {
        std::string head = event;
        if (!action.empty())
        {
            head.append(" ").append(action);
        }
        segs.push_back(head);
    }

    const auto key_seg = build_kvs(key);
    if (!key_seg.empty())
    {
        segs.push_back(key_seg);
    }

    std::ostringstream result_seg;
    if (!result.empty())
    {
        result_seg << "result=" << result;
    }
    if (!reason.empty())
    {
        if (!result_seg.str().empty())
        {
            result_seg << ' ';
        }
        result_seg << "reason=" << reason;
    }
    if (!result_seg.str().empty())
    {
        segs.push_back(result_seg.str());
    }

    const auto tail_seg = build_kvs(tail);
    if (!tail_seg.empty())
    {
        segs.push_back(tail_seg);
    }

    return join_segments(segs);
}

static void log_line(Level lvl, const std::string& source, const std::string& message)
{
    std::cout << timestamp() << " - " << level_name(lvl) << " - [" << source << "] - " << message << '\n';
    std::cout.flush();
}

int main()
{
    constexpr const char* kSource = "DETECT";
    long frame_id = 0;

    log_line(
        Level::Info,
        kSource,
        build_message(
            "startup",
            "",
            {{"backend", "ncnn"}},
            "ok",
            "stub_demo",
            {}));

    while (true)
    {
        const double ts = static_cast<double>(now_ms());

        std::cout
            << "[ NCNN ] "
            << "{"
            << "\"ts\":" << ts << ","
            << "\"frame_id\":" << frame_id << ","
            << "\"detections\":[{\"class_id\":2,\"cls\":\"power\",\"conf\":0.85,\"xyxy\":[10,20,200,220]}],"
            << "\"saved_image\":\"\""
            << "}"
            << "\n";
        std::cout.flush();

        log_line(
            Level::Debug,
            kSource,
            build_message(
                "ncnn_output",
                "emit",
                {
                    {"frame_id", std::to_string(frame_id)},
                    {"det_count", "1"},
                    {"class_id", "2"},
                    {"cls", "power"},
                    {"conf", "0.85"},
                    {"bbox_xyxy", "10,20,200,220"},
                    {"backend", "ncnn"},
                },
                "ok",
                "",
                {{"id_frame", std::to_string(frame_id)}}));

        ++frame_id;
        std::this_thread::sleep_for(std::chrono::milliseconds(1000));
    }
}