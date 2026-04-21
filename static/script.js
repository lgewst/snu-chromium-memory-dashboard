document.addEventListener('DOMContentLoaded', () => {
    // Sidebar toggle functionality
    const sidebar = document.getElementById('sidebar');
    const toggleBtnSidebar = document.getElementById('sidebarToggle');

    // Remove the init class and apply the proper collapsed class if needed
    if (document.documentElement.classList.contains('sidebar-collapsed-init')) {
        sidebar.classList.add('collapsed');
        document.documentElement.classList.remove('sidebar-collapsed-init');
    }

    function toggleSidebar() {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
    }

    if (toggleBtnSidebar) toggleBtnSidebar.onclick = toggleSidebar;

    // DOM Element References
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const statusDiv = document.getElementById('status');
    const resultsTableBody = document.querySelector('#resultsTable tbody');
    const memoryChartCanvas = document.getElementById('memoryChart');
    
    if (!startBtn || !stopBtn || !statusDiv || !resultsTableBody || !memoryChartCanvas) {
        console.log("Dashboard elements not found, skipping dashboard initialization.");
        return;
    }

    const ctx = memoryChartCanvas.getContext('2d');
    
    let memoryChart;
    let lastResultsCount = -1;

    /**
     * Fetches the current execution status from the backend.
     * Updates the status text and enables/disables control buttons.
     */
    const updateStatus = async () => {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();
            if (data.is_running) {
                // Show detailed status (e.g., "Building", "Measuring URL...") next to the task ID
                statusDiv.innerText = `Status: ${data.detailed_status || 'Running'} (${data.current_task || 'initializing'})`;
                startBtn.disabled = true;
                stopBtn.disabled = false;
            } else {
                statusDiv.innerText = `Status: ${data.detailed_status || 'Idle'}`;
                startBtn.disabled = false;
                stopBtn.disabled = true;
            }
        } catch (err) {
            console.error('Failed to fetch status:', err);
        }
    };

    /**
     * Fetches all measurement results and updates the UI.
     * Includes logic to avoid unnecessary re-renders if data hasn't changed.
     */
    const fetchResults = async () => {
        try {
            const response = await fetch('/api/results');
            const results = await response.json();
            
            // Only update if data count changed to prevent chart animation jumping
            if (results.length !== lastResultsCount) {
                lastResultsCount = results.length;
                updateTable(results);
                updateChart(results);
            }
        } catch (err) {
            console.error('Failed to fetch results:', err);
        }
    };

    /**
     * Helper to calculate median of an array.
     */
    const calculateMedian = (arr) => {
        if (!arr.length) return 0;
        const sorted = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    };

    /**
     * Rebuilds the results table with the latest data.
     */
    const updateTable = (results) => {
        resultsTableBody.innerHTML = '';
        results.forEach(res => {
            const row = document.createElement('tr');
            
            // For each iteration, get the peak memory for each URL.
            // Then find the median of those peak values across all iterations.
            const allIterationPeaks = [];
            res.memory_results.forEach(iter => {
                if (iter.urls) {
                    Object.values(iter.urls).forEach(urlData => {
                        // Priority: New 'peak' field, fallback to old 'all' array, then direct number
                        if (urlData && urlData.peak !== undefined) {
                            allIterationPeaks.push(urlData.peak);
                        } else if (urlData && Array.isArray(urlData.all)) {
                            allIterationPeaks.push(Math.max(...urlData.all));
                        } else if (typeof urlData === 'number') {
                            allIterationPeaks.push(urlData);
                        }
                    });
                }
            });
            
            const medianPeak = calculateMedian(allIterationPeaks).toFixed(2);

            row.innerHTML = `
                <td>${res.id}</td>
                <td>${res.build_time.toFixed(2)}</td>
                <td>${medianPeak}</td>
                <td>${res.build_flags.join(' ') || 'None'}</td>
                <td>${res.runtime_flags.join(' ') || 'None'}</td>
                <td>${new Date(res.timestamp * 1000).toLocaleString()}</td>
            `;
            resultsTableBody.appendChild(row);
        });
    };

    /**
     * Updates the Chart.js visualization.
     */
    const updateChart = (results) => {
        const labels = results.map(r => r.id);
        const dataPoints = results.map(res => {
            const allIterationPeaks = [];
            res.memory_results.forEach(iter => {
                if (iter.urls) {
                    Object.values(iter.urls).forEach(urlData => {
                        if (urlData && urlData.peak !== undefined) {
                            allIterationPeaks.push(urlData.peak);
                        } else if (urlData && Array.isArray(urlData.all)) {
                            allIterationPeaks.push(Math.max(...urlData.all));
                        } else if (typeof urlData === 'number') {
                            allIterationPeaks.push(urlData);
                        }
                    });
                }
            });
            return calculateMedian(allIterationPeaks);
        });

        // Clear existing chart instance if it exists
        if (memoryChart) {
            memoryChart.destroy();
        }

        memoryChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Average Memory Usage (MB)',
                    data: dataPoints,
                    backgroundColor: 'rgba(54, 162, 235, 0.5)',
                    borderColor: 'rgba(54, 162, 235, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                animation: true, 
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Memory (MB)' }
                    },
                    x: {
                        title: { display: true, text: 'Feature ID' }
                    }
                }
            }
        });
    };

    // Event Listeners for UI Buttons
    startBtn.addEventListener('click', async () => {
        await fetch('/api/start', { method: 'POST' });
        updateStatus();
    });

    stopBtn.addEventListener('click', async () => {
        // Disable both buttons immediately to prevent multiple clicks
        startBtn.disabled = true;
        stopBtn.disabled = true;
        statusDiv.innerText = 'Status: Stopping processes... please wait.';
        
        await fetch('/api/stop', { method: 'POST' });
        updateStatus();
    });

    // Polling intervals for real-time dashboard updates
    setInterval(() => {
        updateStatus();
        fetchResults();
    }, 3000);

    // Initial load
    updateStatus();
    fetchResults();

    // Initial load
    updateStatus();
    fetchResults();

    // ================================================================================================
    // --- Vitals Widget Logic ---
    // ================================================================================================
    const vitalsSummary = document.getElementById('vitalsSummary');
    const vitalsIndicator = document.getElementById('vitalsIndicator');
    const vitalCpu = document.getElementById('vitalCpu');
    const vitalRam = document.getElementById('vitalRam');
    const vitalDisk = document.getElementById('vitalDisk');
    const vitalUptime = document.getElementById('vitalUptime');
    const vitalsContainer = document.querySelector('.vitals-container');

    let isVitalsExpanded = false;
    let isServerMode = false;

    // 1. 위젯 요약창 클릭 시 Toggle 동작
    if (vitalsSummary && vitalsContainer) {
        vitalsSummary.addEventListener('click', (event) => {
            event.stopPropagation(); 
            vitalsContainer.classList.toggle('expanded');
            isVitalsExpanded = vitalsContainer.classList.contains('expanded');
        });
    }

    // 2. 위젯 바깥 화면을 클릭하면 디테일 창이 닫히도록 처리
    document.addEventListener('click', (event) => {
        if (vitalsContainer && vitalsContainer.classList.contains('expanded')) {
            // 클릭한 곳이 위젯 내부가 아니라면
            if (!vitalsContainer.contains(event.target)) {
                vitalsContainer.classList.remove('expanded');
                isVitalsExpanded = false;
            }
        }
    });

    /**
     * Mocking
     */
    const fetchLocalMockVitals = async () => {
        return new Promise(resolve => {
            setTimeout(() => {
                resolve({
                    cpu_percent: Math.floor(Math.random() * 40) + 10, // 10~50%
                    ram_percent: Math.floor(Math.random() * 30) + 40, // 40~70%
                    disk_free_gb: Math.floor(Math.random() * 50) + 20, // 20~70GB
                    uptime: `${Math.floor(Math.random() * 12)}h ${Math.floor(Math.random() * 60).toString().padStart(2, '0')}m`
                });
            }, 100); 
        });
    };
    const fetchServerMockVitals = async () => {
        return new Promise(resolve => {
            setTimeout(() => {
                resolve({
                    cpu_percent: Math.floor(Math.random() * 30) + 60, // 60~90% (바쁜 서버)
                    ram_percent: Math.floor(Math.random() * 20) + 70, // 70~90%
                    disk_free_gb: Math.floor(Math.random() * 500) + 100, // 100~600GB
                    uptime: `${Math.floor(Math.random() * 50) + 30} days` // 서버다운 긴 업타임
                });
            }, 100); 
        });
    };


    const updateVitalsUI = (data) => {
        if (!vitalsIndicator) return;

        vitalCpu.innerText = `${data.cpu_percent}%`;
        vitalRam.innerText = `${data.ram_percent}%`;
        vitalDisk.innerText = `${data.disk_free_gb}GB`;
        vitalUptime.innerText = data.uptime;

        let statusColor = 'var(--vital-good)'; 
        let isDanger = data.cpu_percent >= 90 || data.ram_percent >= 90 || data.disk_free_gb < 20;
        let isWarning = data.cpu_percent >= 70 || data.ram_percent >= 70 || data.disk_free_gb < 50;

        if (isDanger) statusColor = 'var(--vital-danger)';
        else if (isWarning) statusColor = 'var(--vital-warning)';

        vitalsIndicator.style.backgroundColor = statusColor;
    };


    const pollVitals = async () => {
        try {
            const data = isServerMode ? await fetchServerMockVitals() : await fetchLocalMockVitals();
            updateVitalsUI(data);
        } catch (error) {
            console.error("Vitals Error:", error);
            if (vitalsIndicator) vitalsIndicator.style.backgroundColor = '#95a5a6';
        } finally {
            const nextPollInterval = isVitalsExpanded ? 3000 : 9000;
            setTimeout(pollVitals, nextPollInterval);
        }
    };

    const syncVitalsLabel = async () => {
        const vitalsLabel = document.getElementById('vitalsLabel');
        if (!vitalsLabel) return;

        try {
            const response = await fetch('/api/settings');
            const settings = await response.json();

            isServerMode = !!settings.use_ssh;
            
            if (settings.use_ssh) {
                vitalsLabel.innerText = 'Server';
            } else {
                vitalsLabel.innerText = 'Local';
            }
        } catch (error) {
            console.error("Failed to sync settings for vitals label:", error);
            // 에러 시 기본값
            // @@@@@@ TODO @@@@@@@@@@@@@@@
            vitalsLabel.innerText = 'Local(ssh error)';
            isServerMode = false;
        }
    };

    syncVitalsLabel().then(() => {
    if (vitalsContainer) {
        pollVitals();
    }
});
    // ================================================================================================
    // --- end Vitals Widget Logic ---
    // ================================================================================================
});
