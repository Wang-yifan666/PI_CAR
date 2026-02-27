cmake_minimum_required(VERSION 3.11)

project(detector_cpp)

set(CMAKE_CXX_STANDARD 17)  # c++17 
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# add_executable(detector_stub stub.cpp)  # 测试用

# add_executable(detector_ncnn detector_ncnn.cpp) # 实际用

if (NOT CMAKE_BUILD_TYPE)   # 默认设为 Release
  set(CMAKE_BUILD_TYPE Release) 
endif()

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ncnn(submodule)
set(NCNN_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(NCNN_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(NCNN_VULKAN OFF CACHE BOOL "" FORCE)
add_subdirectory(third_party/ncnn)

# OpenCV from vcpkg: prefer CONFIG mode
set(OpenCV_DIR "D:/opencv/opencv_4.12/build/x64/vc16/lib")
find_package(OpenCV CONFIG REQUIRED)

add_executable(detector_stub stub.cpp)
add_executable(detector_ncnn detector_ncnn.cpp)

# Prefer imported target if available:
# If this errors, switch to the module list version below
target_link_libraries(detector_stub PRIVATE ncnn ${OpenCV_LIBS})
target_link_libraries(detector_ncnn PRIVATE ncnn ${OpenCV_LIBS})

# If OpenCV::OpenCV is NOT available, use this instead:
# target_link_libraries(detector_stub PRIVATE ncnn opencv_core opencv_imgproc opencv_highgui opencv_videoio)
# target_link_libraries(detector_ncnn PRIVATE ncnn opencv_core opencv_imgproc opencv_highgui opencv_videoio)

# speed (cross-compiler)
target_compile_options(detector_ncnn PRIVATE
  $<$<CXX_COMPILER_ID:MSVC>:/O2>
  $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-O3>
)