#include <opencv2/opencv.hpp>
#include "net.h"
#include <chrono>
#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <algorithm>
#include <cmath>

static double now_ms()
{
    using namespace std::chrono;
    return (double)duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

static std::vector< std::string > load_classes( const std::string& path )
{
    std::ifstream ifs(path ) ;
    std::vector< std::string > names ;
    std::string line ;

    while ( std::getline( ifs , line ) )
    {
        if ( !line.empty() )
            names.push_back( line ) ;
    }

    return names ;
}

// 行列式 ， 方便后续 NMS 计算 IOU 和输出 JSON 格式的检测结果
struct Det       
{
    int cid;
    float conf;
    float x1, y1, x2, y2;
};

// 计算两个检测框的 IOU
static float iou(const Det& a, const Det& b)   
{
    float xx1 = std::max(a.x1, b.x1) ;
    float yy1 = std::max(a.y1, b.y1) ;
    float xx2 = std::min(a.x2, b.x2) ;
    float yy2 = std::min(a.y2, b.y2) ;

    float w = std::max(0.f, xx2 - xx1) ;
    float h = std::max(0.f, yy2 - yy1) ;
    float inter = w * h;

    float areaA = std::max(0.f, a.x2 - a.x1) * std::max(0.f, a.y2 - a.y1) ;
    float areaB = std::max(0.f, b.x2 - b.x1) * std::max(0.f, b.y2 - b.y1) ;

    return inter / (areaA + areaB - inter + 1e-9f) ;
}

// 按置信度排序，保留前topk个检测结果
static std::vector<Det> nms(std::vector<Det> dets, float iou_thres, int topk = 200)   
{
    std::sort(dets.begin(), dets.end(),
              [](const Det& a, const Det& b) { return a.conf > b.conf; }) ; 

    if ( dets.size() > topk ) 
        dets.resize(topk) ;

    std::vector<Det> keep ;
    std::vector<char> removed(dets.size(), 0) ;

    for ( size_t i = 0 ; i < dets.size() ; ++ i )
    {
        if (removed[i]) 
            continue ;
        keep.push_back(dets[i]) ;

        for ( size_t j = i + 1 ; j < dets.size() ; ++ j )
        {
            if (removed[j]) 
                continue ;
            if (dets[i].cid != dets[j].cid) 
                continue ;

            if (iou(dets[i], dets[j]) > iou_thres)
                removed[j] = 1;
        }
    }
    return keep;
}

static bool starts_with( const std::string& str , const std::string& prefix )
{
    return str.rfind( prefix , 0 ) == 0 ;
}

int main( int argc , char** argv )
{
    std::string param_path , bin_path , classes_path ;
    std::string out_name = "output" ; // --out=xxx 来指定输出文件名，默认为 output.jpg
    std::string source = "camera:0" ; // --source=xxx 来指定输入源，默认为摄像头 0

    int imgsz = 640 ;          // 图片大小
    int threads = 4 ;          // 线程数
    float conf_thres = 0.25 ;  // 置信度阈值
    bool debug = false;        // 是否输出调试信息
    float nms_thres = 0.45f;   // NMS IOU 阈值
    int topk = 50;             // NMS 保留前 topk 个检测结果

    for ( int i = 1 ; i < argc ; ++ i )
    {
        std::string a = argv[i];
        if ( starts_with(a, "--param=") )          // --param=xxx 来指定 param 文件路径
            param_path = a.substr(8) ;
        else if ( starts_with(a, "--bin=") )       // --bin=xxx 来指定 bin 文件路径
            bin_path = a.substr(6) ;
        else if ( starts_with(a, "--classes=") )   // --classes=xxx 来指定类别文件路径
            classes_path = a.substr(10) ;
        else if ( starts_with(a, "--out=") )       // --out=xxx 来指定输出文件名，默认为 output.jpg
            out_name = a.substr(6) ;
        else if ( starts_with(a, "--source=") )    // --source=xxx 来指定输入源，默认为摄像头 0
            source = a.substr(9) ;
        else if ( starts_with(a, "--imgsz=") )     // --imgsz=xxx 来指定输入图像尺寸，默认为 640
            imgsz = std::stoi(a.substr(8)) ;
        else if ( starts_with(a, "--threads=") )   // --threads=xxx 来指定线程数，默认为 4，设置为 0 则使用系统默认线程数
            threads = std::stoi(a.substr(10)) ;
        else if ( starts_with(a, "--conf=") )      // --conf=xxx 来指定置信度阈值，默认为 0.25
            conf_thres = std::stof(a.substr(7)) ;
        else if ( starts_with(a, "--nms=") )       // --nms=xxx 来指定 NMS IOU 阈值，默认为 0.45
            nms_thres = std::stof(a.substr(6)) ;
        else if ( starts_with(a, "--topk=") )      // --topk=xxx 来指定 NMS 保留前 topk 个检测结果，默认为 50
            topk = std::stoi(a.substr(7)) ;
        else if ( starts_with(a, "--debug=") )     // --debug=1 来开启调试信息输出，默认为 0（关闭）
            debug = (std::stoi(a.substr(8)) != 0) ;
    }

    if ( param_path.empty() || bin_path.empty() )
    {
        std::cerr << "Usage: detector_ncnn --param=xxx.param --bin=xxx.bin "
                     "[--classes=classes.txt] [--source=camera:0|video:xx.mp4] "
                     "[--out=output] [--imgsz=640] [--threads=4] [--conf=0.25]\n";
        return 2;
    }

    std::vector< std::string > cls_names ;  

    if ( !classes_path.empty() ) 
        cls_names = load_classes(classes_path);

    cv::VideoCapture cap ;

    if ( starts_with( source , "camera:") )
    {
        int cam_id = std::stoi( source.substr(7) ) ;
        cap.open( cam_id ) ;
    }
    else if ( starts_with( source , "video:") )
    {
        cap.open(source.substr(6)) ;
    }
    else
    {
        std::cerr << "[DECTCTOR_NCNN] unknown source: " << source << "\n" ;
        return 3 ;
    }

    if ( !cap.isOpened() )
    {
        std::cerr << "[DECTCTOR_NCNN] failed to open source: " << source << "\n" ;
        return 4 ;
    }

    ncnn::Net net ;
    net.opt.num_threads = threads ; 
    net.opt.use_packing_layout = true ;
    net.opt.lightmode = true ; 

    if ( net.load_param( param_path.c_str()) != 0 )
    {
        std::cerr << "[DECTCTOR_NCNN] failed to load param file: " << param_path << "\n" ;
        return 5 ;
    }

    if ( net.load_model( bin_path.c_str()) != 0 )
    {
        std::cerr << "[DECTCTOR_NCNN] failed to load bin file: " << bin_path << "\n" ;
        return 6 ;
    }

    if ( debug )
    {
        std::cerr << "[detector_ncnn] started. out_name=" << out_name
              << " imgsz=" << imgsz << " threads=" << threads << "\n" ;
    }

    long frame_id = 0 ;

    while( true )
    {
        cv::Mat frame ; 
        
        if ( !cap.read( frame ) || frame.empty() )
            break ;

        const int img_w = frame.cols ;
        const int img_h = frame.rows ;

        // OpenCV reads BGR; convert to RGB to match the Python ONNX preprocessing path.
        ncnn::Mat in = ncnn::Mat::from_pixels_resize(
            frame.data, ncnn::Mat::PIXEL_BGR2RGB, img_w, img_h, imgsz, imgsz ) ;

        // 归一化到 [0,1]
        const float norm_vals[3] = {1/255.f, 1/255.f, 1/255.f};
        in.substract_mean_normalize(nullptr, norm_vals);

        ncnn::Extractor ex = net.create_extractor();
        ex.input("in0", in);  

        ncnn::Mat out;
        int ret = ex.extract(out_name.c_str(), out);
        if (ret != 0) 
        {
            std::cerr << "[DETECTOR_NCNN] extract failed. out_name=" << out_name
                      << " (try --out=xxx)\n";
            std::cout << "[ NCNN ]{\"ts\":" << now_ms()
                      << ",\"frame_id\":" << frame_id
                      << ",\"img_w\":" << img_w
                      << ",\"img_h\":" << img_h
                      << ",\"type\":\"detection\",\"detections\":[]}\n" ;
            std::cout.flush() ;
            frame_id ++ ;
            continue ;
        }

        // DEBUG
        if ( debug )
        {
            std::cerr << "[detector_ncnn] out(" << out_name << ") shape: dims=" << out.dims
                  << " w=" << out.w << " h=" << out.h << " c=" << out.c << "\n" ;

            for ( int i = 0; i < std::min(3, out.h) ; ++ i )
            {
                const float* p = out.row(i) ;
                std::cerr << "[detector_ncnn] row" << i << ": " ;
                for ( int j = 0 ; j < std::min(out.w, 8) ; ++ j )
                    std::cerr << p[j] << " " ;
                std::cerr << "\n" ;
            }
        }

        std::vector<Det> cand ;

        auto clampf = [](float v, float lo, float hi) 
        {
            return std::max(lo, std::min(v, hi)) ;
        };

        // scale: 640x640 -> original img_w x img_h
        float sx = (float)img_w / (float)imgsz ;
        float sy = (float)img_h / (float)imgsz ;

        for (int i = 0; i < out.h; ++i)
        {
            const float* p = out.row(i) ;
        
            if (out.w < 6)
                break ;
        
            float cx = p[0] ;
            float cy = p[1] ;
            float bw = p[2] ;
            float bh = p[3] ;

            float obj = p[4] ; 

            // Some converted models may emit NaN/Inf on tail rows; drop invalid rows early.
            if (!std::isfinite(cx) || !std::isfinite(cy) || !std::isfinite(bw) || !std::isfinite(bh) || !std::isfinite(obj))
                continue;

            int cid = 0 ;
            float cls_best = p[5] ;
            if (!std::isfinite(cls_best))
                continue;
            for ( int k = 1; k < (out.w - 5) ; ++ k ) 
            {
                float s = p[5 + k] ;
                if (!std::isfinite(s))
                    continue;
                if (s > cls_best)
                {
                    cls_best = s ;
                    cid = k ;
                }
            }

            float score = obj * cls_best ;

            if (!std::isfinite(score) || score < conf_thres)
                continue;

            if ( debug ) 
            {
                std::cerr << "row" << i
                          << " obj=" << obj
                          << " cls_best=" << cls_best
                          << " score=" << score
                          << " cid=" << cid
                          << "\n";
            }
            
            // cxcywh -> xyxy (on imgsz scale)
            float x1 = cx - bw * 0.5f ;
            float y1 = cy - bh * 0.5f ;
            float x2 = cx + bw * 0.5f ;
            float y2 = cy + bh * 0.5f ;
        
            if (x1 > x2) 
                std::swap(x1, x2) ;
            if (y1 > y2) 
                std::swap(y1, y2) ;
        
            x1 *= sx ; 
            x2 *= sx ;
            y1 *= sy ; 
            y2 *= sy ;
        
            x1 = clampf(x1, 0.f, img_w - 1.f) ;
            x2 = clampf(x2, 0.f, img_w - 1.f) ;
            y1 = clampf(y1, 0.f, img_h - 1.f) ;
            y2 = clampf(y2, 0.f, img_h - 1.f) ;
        
            // 去掉小框
            if ( (x2 - x1) < 2.f || (y2 - y1) < 2.f )
                continue ;
        
            cand.push_back(Det{cid, score, x1, y1, x2, y2}) ;
        }

        // NMS
        auto keep = nms(cand, nms_thres, std::max(topk * 4, 200));

        std::vector<std::string> det_json;
        det_json.reserve(keep.size());

        if ( keep.size() > topk )
            keep.resize(topk) ;

        for (const auto& d : keep)
        {
            std::string cname = (d.cid >= 0 && d.cid < (int)cls_names.size())
                                ? cls_names[d.cid]
                                : std::to_string(d.cid);
        
            std::ostringstream one;
            one << "{\"class_id\":" << d.cid
                << ",\"cls\":\"" << cname << "\""
                << ",\"conf\":" << d.conf
                << ",\"xyxy\":[" << (int)std::round(d.x1) << ","
                                << (int)std::round(d.y1) << ","
                                << (int)std::round(d.x2) << ","
                                << (int)std::round(d.y2) << "]}" ;
            det_json.push_back(one.str());
        }
        
        std::ostringstream js ;
        js << "{\"ts\":" << now_ms()
           << ",\"frame_id\":" << frame_id
           << ",\"img_w\":" << img_w
           << ",\"img_h\":" << img_h
           << ",\"type\":\"detection\""
           << ",\"detections\":[" ;

        for ( size_t i = 0; i < det_json.size(); ++ i ) 
        {
            if (i) 
                js << ",";
            js << det_json[i] ;
        }
        js << "]}" ;

        std::cout << "[ NCNN ]" << js.str() << "\n" ;
        std::cout.flush() ;

        ++ frame_id ;
    }

    std::cerr << "[DETECTOR_NCNN] EOF\n" ;

    return 0 ;
}
