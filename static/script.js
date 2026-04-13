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
                Object.values(iter.urls).forEach(urlData => {
                    // urlData is now {peak: X, all: [...]}
                    if (urlData && urlData.peak) {
                        allIterationPeaks.push(urlData.peak);
                    } else if (typeof urlData === 'number') {
                        allIterationPeaks.push(urlData); // Fallback for old data
                    }
                });
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
                Object.values(iter.urls).forEach(urlData => {
                    if (urlData && urlData.peak) {
                        allIterationPeaks.push(urlData.peak);
                    } else if (typeof urlData === 'number') {
                        allIterationPeaks.push(urlData);
                    }
                });
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
});
